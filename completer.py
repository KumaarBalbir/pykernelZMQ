# Importing necessary modules and classes
from __future__ import print_function  # Allow the use of print as a function
import itertools
# import readline
try:
    import pyreadline as readline
except ImportError:
    import readline
import rlcompleter
import time
import session

class KernelCompleter(object):
    """Kernel-side completion machinery."""
    def __init__(self, namespace):
        # Initialize the KernelCompleter with a Python namespace
        self.namespace = namespace
        # Create a rlcompleter.Completer instance for tab completion
        self.completer = rlcompleter.Completer(namespace)

    def complete(self, line, text):
        # Perform tab completion for a given line and text
        matches = []  # Store the completion matches
        complete = self.completer.complete  # Get the completion function
        for state in itertools.count():
            comp = complete(text, state)  # Get the next completion
            if comp is None:
                break  # No more completions
            matches.append(comp)  # Append the completion to the list
        return matches  # Return the list of completion matches

class ClientCompleter(object):
    """Client-side completion machinery."""

    def __init__(self, client, session, socket):
        # Initialize the ClientCompleter with a client, session, and socket
        self.client = client
        self.session = session
        self.socket = socket
        self.matches = []  # Initialize an empty list for completion matches

    def request_completion(self, text):
        # Get the full line to provide to the kernel for completion
        line = readline.get_line_buffer()
        # Send a completion request message to the kernel
        msg = self.session.send(self.socket, 'complete_request', dict(text=text, line=line))

        # Give the kernel up to 0.5s to respond with completion matches
        for i in range(5):
            rep = self.session.recv(self.socket)  # Receive the kernel's response
            if rep is not None and rep.msg_type == 'complete_reply':
                matches = rep.content.matches  # Get the completion matches
                break
            time.sleep(0.1)
        else:
            # Timeout occurred if we didn't receive a complete_reply
            print('TIMEOUT')  # Display a warning message (not visible to user)
            matches = None  # Set matches to None in case of a timeout
        return matches  # Return the completion matches received from the kernel

    def complete(self, text, state):
        # This method is called multiple times with increasing state values
        # State=0: Compute all the completion matches and store them
        # State=1, 2, ...: Return the stored matches for each state
        if self.client.backgrounded > 0:
            # If background tasks are active, don't perform completion
            print("\n[Not completing, background tasks active]")
            print(readline.get_line_buffer(), end='')
            return None

        if state == 0:
            # On the first state, compute all the completion matches
            matches = self.request_completion(text)
            if matches is None:
                self.matches = []  # Clear matches on timeout
                print('WARNING: Kernel timeout on tab completion.')
            else:
                self.matches = matches  # Store the computed matches

        try:
            return self.matches[state]  # Return matches for the current state
        except IndexError:
            return None  # No more matches for this state
