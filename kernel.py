#!/usr/bin/env python
"""A simple interactive kernel that talks to a frontend over 0MQ.

Things to do:

* Finish implementing `raw_input`.
* Implement `set_parent` logic. Right before doing exec, the Kernel should
  call set_parent on all the PUB objects with the message about to be executed.
* Implement random port and security key logic.
* Implement control messages.
* Implement event loop and poll version.
"""

import builtins as __builtin__
import sys
import time
import traceback

from code import InteractiveConsole as CommandCompiler  # Use InteractiveConsole instead of CommandCompiler

import zmq

from session import Session, Message, extract_header
from completer import KernelCompleter

class OutStream(object):
    """A file-like object that publishes the stream to a 0MQ PUB socket."""

    def __init__(self, session, pub_socket, name, max_buffer=200):
        self.session = session
        self.pub_socket = pub_socket
        self.name = name
        self._buffer = []
        self._buffer_len = 0
        self.max_buffer = max_buffer
        self.parent_header = {}

    def set_parent(self, parent):
        self.parent_header = extract_header(parent)

    def close(self):
        self.pub_socket = None

    def flush(self):
        if self.pub_socket is None:
            raise ValueError('I/O operation on a closed file')
        else:
            if self._buffer:
                data = ''.join(self._buffer)
                content = {'name': self.name, 'data': data}
                msg = self.session.msg('stream', content=content,
                                       parent=self.parent_header)
                self.pub_socket.send_json(msg)
                self._buffer_len = 0
                self._buffer = []

    def isatty(self):
        return False

    def __next__(self):
        raise IOError('Read not supported on a write-only stream.')

    def read(self, size=None):
        raise IOError('Read not supported on a write-only stream.')

    readline = read

    def write(self, s):
        if self.pub_socket is None:
            raise ValueError('I/O operation on a closed file')
        else:
            self._buffer.append(s)
            self._buffer_len += len(s)
            self._maybe_send()


    def _maybe_send(self):
        if '\n' in self._buffer[-1]:
            self.flush()
        elif self._buffer_len > self.max_buffer:
            self.flush()

    def writelines(self, sequence):
        if self.pub_socket is None:
            raise ValueError('I/O operation on a closed file')
        else:
            for s in sequence:
                self.write(s)


class DisplayHook(object):

    def __init__(self, session, pub_socket):
        self.session = session
        self.pub_socket = pub_socket
        self.parent_header = {}

    def __call__(self, obj):
        if obj is None:
            return

        __builtin__._ = obj
        msg = self.session.msg('pyout', {'data': repr(obj)},
                               parent=self.parent_header)
        self.pub_socket.send_json(msg)

    def set_parent(self, parent):
        self.parent_header = extract_header(parent)


class RawInput(object):

    def __init__(self, session, socket):
        self.session = session
        self.socket = socket

    def __call__(self, prompt=None):
        msg = self.session.msg('raw_input')
        self.socket.send_json(msg)
        while True:
            try:
                reply = self.socket.recv_json(zmq.NOBLOCK)
            except zmq.ZMQError as e:
                if e.errno == zmq.EAGAIN:
                    pass
                else:
                    raise
            else:
                break
        return reply['content']['data']


class Kernel(object):

    def __init__(self, session, reply_socket, pub_socket):
        self.session = session
        self.reply_socket = reply_socket
        self.pub_socket = pub_socket
        self.user_ns = {}
        self.history = []
        self.compiler = CommandCompiler()
        self.completer = KernelCompleter(self.user_ns)

        # Build dict of handlers for message types
        self.handlers = {}
        for msg_type in ['execute_request', 'complete_request']:
            self.handlers[msg_type] = getattr(self, msg_type)

    def abort_queue(self):
        while True:
            try:
                ident = self.reply_socket.recv(zmq.NOBLOCK)
            except zmq.ZMQError as e:
                if e.errno == zmq.EAGAIN:
                    break
            else:
                assert self.reply_socket.rcvmore(), "Unexpected missing message part."
                msg = self.reply_socket.recv_json()
            print("Aborting:")
            print(Message(msg), file=sys.__stdout__)
            msg_type = msg['msg_type']
            reply_type = msg_type.split('_')[0] + '_reply'
            reply_msg = self.session.msg(reply_type, {'status': 'aborted'}, msg)
            print(Message(reply_msg), file=sys.__stdout__)
            self.reply_socket.send(ident, zmq.SNDMORE)
            self.reply_socket.send_json(reply_msg)
            # We need to wait a bit for requests to come in. This can probably
            # be set shorter for true asynchronous clients.
            time.sleep(0.1)

    def execute_request(self, ident, parent):
        try:
            code = parent['content']['code']
        except:
            print("Got a bad message:")
            print(Message(parent), file=sys.__stderr__)
            return
        pyin_msg = self.session.msg('pyin', {'code': code}, parent=parent)
        self.pub_socket.send_json(pyin_msg)
        try:
            comp_code = self.compiler(code, '<zmq-kernel>')
            sys.displayhook.set_parent(parent)
            result = eval(comp_code, self.user_ns, self.user_ns)
        except Exception as e:
            result = 'error'
            etype, evalue, tb = sys.exc_info()
            tb = traceback.format_exception(etype, evalue, tb)
            exc_content = {
                'status': 'error',
                'ename': str(etype),
                'evalue': str(evalue),
                'traceback': tb,
            }
            exc_msg = self.session.msg('error', exc_content, parent)
            self.pub_socket.send_json(exc_msg)
        else:
            reply_content = {
                'status': 'ok',
                'execution_count': parent['content'].get('execution_count', 1),
                'payload': [],
                'user_expressions': {},
            }
            display_data = {
                'data': {
                    'text/plain': repr(result),
                },
                'metadata': {},
            }
            reply_msg = self.session.msg('execute_reply', reply_content, parent)
            display_msg = self.session.msg('display_data', display_data, parent)
            self.pub_socket.send_json(reply_msg)
            self.pub_socket.send_json(display_msg)
        if result == 'error':
            self.abort_queue()

    def complete_request(self, ident, parent):
        matches = {'matches': self.complete(parent),
                   'status': 'ok'}
        completion_msg = self.session.send(self.reply_socket, 'complete_reply',
                                           matches, parent, ident)
        print(completion_msg, file=sys.__stdout__)

    def complete(self, msg):
        return self.completer.complete(msg['content']['line'], msg['content']['text'])

    def start(self):
        while True:
            ident = self.reply_socket.recv()
            assert self.reply_socket.rcvmore(), "Unexpected missing message part."
            msg = self.reply_socket.recv_json()
            omsg = Message(msg)
            print("Received message from frontend2.py:", omsg)
            print(omsg, file=sys.__stdout__)
            handler = self.handlers.get(omsg['msg_type'], None)
            if handler is None:
                print("UNKNOWN MESSAGE TYPE:", omsg, file=sys.__stderr__)
            else:
                handler(ident, omsg)

def main():
    try:
        c = zmq.Context()

        ip = '127.0.0.1'
        port_base = 5555
        connection = ('tcp://%s' % ip) + ':%i'
        rep_conn = connection % port_base
        pub_conn = connection % (port_base + 1)

        print("Starting the kernel...")
        print("On:", rep_conn, pub_conn, file=sys.__stdout__)

        session = Session(username='kernel')
        print("debug 1")
        reply_socket = c.socket(zmq.ROUTER)
        reply_socket.bind(rep_conn)
        print("debug 1.1")

        pub_socket = c.socket(zmq.PUB)
        pub_socket.bind(pub_conn)
        print("debug 1.2")

        stdout = OutStream(session, pub_socket, 'stdout')
        print("debug 1.21")

        stderr = OutStream(session, pub_socket, 'stderr')
        print("debug 1.22")

        sys.stdout = stdout
        print("debug 1.23")

        sys.stderr = stderr
        print("debug 1.3")


        display_hook = DisplayHook(session, pub_socket)
        sys.displayhook = display_hook

        kernel = Kernel(session, reply_socket, pub_socket)
        print("debug 2")

        # For debugging convenience, put sleep and a string in the namespace, so we
        # have them every time we start.
        kernel.user_ns['sleep'] = time.sleep
        kernel.user_ns['s'] = 'Test string'

        print("Use Ctrl-\\ (NOT Ctrl-C!) to terminate.")
        kernel.start()
    except Exception as e:
        print("An error occurred:", str(e))
    finally:
        if 'kernel' in locals():
            # Close sockets and perform cleanup
            reply_socket.close()
            pub_socket.close()
            c.term()

if __name__ == '__main__':
    main()
