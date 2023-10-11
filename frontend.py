"""A simple frontend for interacting with the kernel over 0MQ."""

import zmq


def main():
    c = zmq.Context()

    ip = '127.0.0.1'
    port_base = 5555
    connection = 'tcp://%s' % ip + ':%i'
    req_conn = connection % port_base

    print("Starting the frontend...")
    print("Connecting to the kernel at:", req_conn)

    req_socket = c.socket(zmq.DEALER)
    req_socket.connect(req_conn)

    try:
        while True:
            code = input("Enter Python code (or 'exit' to quit): ")
            if code == 'exit':
                break

            # Send a request to the kernel to execute the code
            req_socket.send(b'', zmq.SNDMORE)
            req_socket.send_json({
                'msg_type': 'execute_request',
                'content': {
                    'code': code
                }
            })

            # Wait for and receive the reply from the kernel
            reply = req_socket.recv_json()

            if 'content' in reply and 'data' in reply['content']:
                print("Kernel Output:")
                print(reply['content']['data'])
            else:
                print("No output received from the kernel.")

    except KeyboardInterrupt:
        pass
    finally:
        req_socket.close()
        c.term()


if __name__ == '__main__':
    main()
