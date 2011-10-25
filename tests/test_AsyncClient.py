#! /usr/bin/env python

from wsgiref.validate import validator
from unittest import TestCase
from gevent import monkey
from gevent import socket
from gevent import pywsgi
import gevent


class TestAsyncClient(TestCase):

    @staticmethod
    def application(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return ['hello', 'world']

    def setUp(self):
        self.application = validator(self.application)
        self.server = pywsgi.WSGIServer(('127.0.0.1', 0), self.application)
        self.server.start()
        self.port = self.server.server_port

    def tearDown(self):
        timeout = gevent.Timeout(0.5,RuntimeError("Timeout trying to stop server"))
        timeout.start()
        try:
            self.server.stop()
        finally:
            timeout.cancel()

    def connect(self):
        return socket.create_connection(('127.0.0.1', self.port))

    def test_connect(self):
        sock = self.connect()
        sock.send('GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        print sock.recv(1500)

        

