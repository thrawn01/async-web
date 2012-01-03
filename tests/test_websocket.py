from asyncweb.websocket import WebSocket
from asyncweb import AsyncClient, AsyncServer
from unittest import TestCase
import gevent


class TestWebSocketServer(TestCase):

    def connect(self, host, port):
        return socket.create_connection((host, port))
   

    def stop(self, server):
        timeout = gevent.Timeout(0.5,RuntimeError("Timeout trying to stop server"))
        timeout.start()
        try:
            server.stop()
        finally:
            timeout.cancel()


    def web_socket_handler(self, request, response):
        response.write("hello world")


    def test_websocket(self):
        web_socket = WebSocket()
        web_socket.add('/', self.web_socket_handler)

        ## Create a simple server with a WebSocket filter that responds with 'Hello World'
        server = AsyncServer(('127.0.0.1', 15001), (web_socket,))
        server.start()

        #try:
        socket = AsyncClient().websocket('http://127.0.0.1:15001/')

        self.assertEquals(socket.headers['connection'], 'Upgrade')
        self.assertEquals(socket.headers['upgrade'], 'websocket')
        self.assertTrue('date' in socket.headers)
        self.assertTrue('sec-websocket-accept' in socket.headers)
        self.assertEquals(socket.status, 101)
        self.assertEquals(socket.reason, 'Switching Protocols')
        self.assertEquals(socket.read(),'hello world')

        #except HTTPException, e:
            #print "HTTP Exception Caught - Status: %s Reason: %s" % (e.status, e.reason)

        self.stop(server)
