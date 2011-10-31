#! /usr/bin/env python

from wsgiref.validate import validator
from unittest import TestCase
from urlparse import urlparse
from gevent import monkey
from gevent import socket
from gevent import pywsgi
from gevent.server import StreamServer
from StringIO import StringIO
from collections import defaultdict
import gevent
import sys

MAX_LINE_LENGTH = 1500

def parse_header(file):
    headers = {}
    while True:
        # Read each header should conform to a format of 'key:value\r\n'
        header_line = file.readline(MAX_LINE_LENGTH + 1)

        # If the header-line is larger than the default MTU, it's bogus
        length = len(header_line)
        if length > MAX_LINE_LENGTH:
            raise RuntimeError('Refusing to parse extremely long header line; %s' % header_line )
        if length == 0:
            raise RuntimeError('HTTP header incorrectly terminated; expected "\\r\\n"')

        # Is this the last line in the header?
        if header_line == '\r\n':
            return headers

        try:
            # If the line does not conform to accepted header format, split or the unpack should let us know
            (key, value) = header_line.split(':', 1)
        except ValueError:
            raise RuntimeError('Malformed header found; %s' % header_line)
        
        # Strip the white space around the key and value
        key = key.strip(); value = value.strip()

        # This doesn't seam performant but is. See Commit 3fa292cf and 38b8c18e8
        if key in headers:
            if isinstance(headers[key], list):
                headers[key].append(value)
            else:
                headers[key] = [headers[key], value]
        else:
            headers[key] = value

    return headers

    

class AsyncServer(StreamServer):

    def __init__(self, listener, filters=None, config=None):
        StreamServer.__init__(self, listener)
        self._filters = filters

    def _next(self,socket, address, count):
        if len(self._filters) > count:
            return self._filters[count](socket,address,lambda socket, address: self._next(socket, address, count+1))
        return (socket, address) 
    
    def handle(self, socket, address):
        return self._next(socket, address, 0)



class Response(object):
    
    def __init__(self, file, version, status, reason, headers):
        self.version = version
        self.status = status
        self.reason = reason
        self.headers = self._normalizeHeaders(headers)
        self.file = file


    def _normalizeHeaders(self,headers):
        try:
            headers['Content-Length'] = int(headers['Content-Length'])
        except ValueError:
            raise RuntimeError('Invalid value for "Content-Length" header; %s' % headers['content-length'][0])
        except KeyError:
            # Missing Content-Length is ok, it might be a chunked response
            pass
        
        return headers


    def _chunk_header(self, file):
        # Read the chunk header line
        line = file.readline(MAX_LINE_LENGTH + 1)
        # If the status-line is larger than the default MTU, it's bogus
        if len(line) > MAX_LINE_LENGTH:
            raise RuntimeError('Refusing to parse extremely long chunk-header; %s' % line )

        # Split out the header and the extension if it exists
        header = line.split(';')
        try:
            # Interpret the length in HEX
            header[0] = int(header[0], 16)
        except KeyError, ValueError:
            raise RuntimeError("Expected hexdecimal length while reading chunk header; %s" % line)
       
        return header


    def read(self, callback=None):
        # XXX: Check for values to read, don't want to block if the user calls us again

        try:
            # RFC2616 Sec 4.4
            # If a Transfer-Encoding header field (section 14.41) is present and has any value other than "identity", 
            # then the transfer-length is defined by use of the "chunked" transfer-coding (section 3.6), 
            # unless the message is terminated by closing the connection. 
            if self.headers['Transfer-Encoding'] != 'identity':
                if self.file.closed: # XXX Does a file object know when a socket is closed?
                    raise KeyError() # Forces a non-chunked read

                result = []
                while True: 
                    # Read chunk header
                    chunked_header = self._chunk_header(self.file)
                    # Read the chunk
                    payload = self.file.read(chunked_header[0])
                    # Read the trailer
                    chunked_trailer = parse_header(self.file)
                    # Run the Call back if one is supplied
                    if callback:
                        payload = callback(self.headers, chunked_header, chunked_trailer, payload )
                    else:
                        result.append(payload)

                    # If no more data in the chunked response
                    if len(payload) == 0:
                        return ''.join(result)
                    
        except KeyError:
            pass

        try:
            return self.file.read(self.headers['Content-Length'])
        except KeyError:
            raise RuntimeError("Unable to read response; missing 'Content-Length' header and 'Transfer-Encoding' is not 'chunked'")

        # XXX: Check for 'Connection: close' header if it exists, close the connection here
    

class AsyncClient(object):
    
    def __init__(self):
        pass


    def log(self, msg):
        sys.stderr.write(msg)


    def parse_uri(self, uri):
        url = urlparse(uri)
        port = int(url.port or 80)
        path = url.path or '/'
        scheme = url[0]

        if not 'http' in scheme:
            raise RuntimeError("Only (http,https) supported")

        return (scheme, url.hostname, port, path)


    def _request_header(self, method, path='/', headers=(), version='HTTP/1.1'):
        headers = [": ".join(x) for x in headers]
        return "%s %s %s\r\n%s\r\n\r\n" % (method, path, version,"\r\n".join(headers))


    def _status_line(self, file):
        # Read the first line of the response ( should be the status-line )
        line = file.readline(MAX_LINE_LENGTH + 1)
        # If the status-line is larger than the default MTU, it's bogus
        if len(line) > MAX_LINE_LENGTH:
            raise RuntimeError('Refusing to parse extremely long status-line; %s' % line )

        try:
            (http_version, status_code, reason_phrase) = line.split(None,2)
        except ValueError:
            raise RuntimeError('Invalid HTTP/1.1 status line in response header; %s' % line)
        
        if http_version != 'HTTP/1.1':
            raise RuntimeError('Server responded with unsupported HTTP Version; Only HTTP/1.1 supported')
        
        try:
            # Although RFC2616 Only mentions 100 through 505, Extensions could utilize up to 999
            status_code = int(status_code)
            if status_code < 100 or status_code > 999:
                raise ValueError()
        except ValueError:
            raise RuntimeError("Invalid status code in HTTP/1.1 status line; %s" % line)
        
        return (11, status_code, reason_phrase.rstrip())
   

    def _parse_response(self, sock):
        # Use a file like object so we can use readline()
        fd = sock.makefile('fb')
        # Parse the status line 
        (version, status, reason) = self._status_line(fd)
        # Parse the response headers
        headers = parse_header(fd)
        # Instanciate the user object Response()
        return Response(fd, version, status, reason, headers)

        
    def get(self, url, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        # Parse the URI
        (scheme, host, port, path) = self.parse_uri(url)
        # Connect to the remote Host
        sock = socket.create_connection((host, port), timeout=timeout)
        # Send the request header
        sock.send(self._request_header('GET', path, (('Host',host),)))
        # Parse the response and return a Request Object      
        return self._parse_response(sock)
   

class TestAsyncClient(TestCase):

    @staticmethod
    def chunked_app(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        yield 'hello world'


    @staticmethod
    def application(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain'),('Content-Length', '11')])
        return ['hello world']


    def setUp(self):
        self.application = validator(self.chunked_app)
        self.server = pywsgi.WSGIServer(('127.0.0.1', 15001), self.application)
        self.server.start()
        self.port = self.server.server_port


    def tearDown(self):
        timeout = gevent.Timeout(0.5,RuntimeError("Timeout trying to stop server"))
        timeout.start()
        try:
            self.server.stop()
        finally:
            timeout.cancel()


    def test_parse_uri(self):
        client = AsyncClient()
        (scheme, host, port, path) = client.parse_uri("http://localhost:1500")
        self.assertEquals(scheme,'http')
        self.assertEquals(host,'localhost')
        self.assertEquals(port,1500)
        self.assertEquals(path,'/')


    def test_parse_uri_raises(self):
        client = AsyncClient()
        self.assertRaises(RuntimeError, client.parse_uri, ("ftp://localhost:1500"))
        self.assertRaises(RuntimeError, client.parse_uri, (""))
        self.assertRaises(RuntimeError, client.parse_uri, ("this is not a url"))


    def test_request_header(self):
        client = AsyncClient()
        request = client._request_header('GET', path='/', headers=(('Host', 'localhost'),))
        self.assertEquals(request, 'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')

    def test_status_line(self):
        client = AsyncClient()
        (version, status, reason) = client._status_line(StringIO('HTTP/1.1 200 OK\r\n'))
        self.assertEquals(version, 11)
        self.assertEquals(status, 200)
        self.assertEquals(reason, 'OK')

    def test_status_line_raises(self):
        client = AsyncClient()

        # Should raise on long status line
        long_line = ''.join([ ' ' for x in range(1,1500) ])
        self.assertRaises(RuntimeError, client._status_line, StringIO(long_line))
        
        # Should raise on garbage status lines
        self.assertRaises(RuntimeError, client._status_line, StringIO('HTTP/1.1-200-OK\r\n'))
        self.assertRaises(RuntimeError, client._status_line, StringIO('blahblah'))
    
        # Should raise on wrong HTTP version
        self.assertRaises(RuntimeError, client._status_line, StringIO('HTTP/1.0 200 OK\r\n'))
        self.assertRaises(RuntimeError, client._status_line, StringIO('HTTP/0.9 200 OK\r\n'))
        
        # Should raise on invalid status code
        self.assertRaises(RuntimeError, client._status_line, StringIO('HTTP/1.1 2001 OK\r\n'))
        self.assertRaises(RuntimeError, client._status_line, StringIO('HTTP/1.1 000 UhOh\r\n'))

        # Should raise on missing values
        self.assertRaises(RuntimeError, client._status_line, StringIO('HTTP/1.1 200 \r\n'))
        self.assertRaises(RuntimeError, client._status_line, StringIO('200 OK\r\n'))

    def test_parse_header(self):
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT\r\n\r\n')

        headers = parse_header(file)
        self.assertEquals(dict(headers), { 'Content-Type': 'text/plain',
                                           'Content-Length': '11',
                                           'Date': 'Fri, 28 Oct 2011 18:40:19 GMT' })

    def test_parse_header_raises(self):

        # Should raise on long header line ( aka, no \r\n )
        long_header = ''.join([ ' ' for x in range(1,1500) ])
        self.assertRaises(RuntimeError, parse_header, StringIO(long_header))
        
        # Should raise on incorrectly terminated headers
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT\r\n')
        self.assertRaises(RuntimeError, parse_header, file)
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT')
        self.assertRaises(RuntimeError, parse_header, file)
        
        # Should raise on malformed, garbage headers
        file = StringIO('Content-Type- text/plain\r\nContent-LengthASDF#$^@#$G@#G#@%H@#$BH@#$#$')
        self.assertRaises(RuntimeError, parse_header, file)


    def test_connect(self):
        resp = AsyncClient().get('http://127.0.0.1:15001/')
        print resp.read()



class TestResponse(TestCase):

    def test_constructor(self):
        resp = Response(StringIO(''), 11, 200, 'OK', {'Content-Length':'11', 'Param':['1','2']})
        self.assertEquals(resp.headers, {'Content-Length': 11, 'Param':['1','2']} )


class TestAsyncServer(TestCase):

    def connect(self, host, port):
        return socket.create_connection((host, port))
    
    def stop(self, server):
        timeout = gevent.Timeout(0.5,RuntimeError("Timeout trying to stop server"))
        timeout.start()
        try:
            server.stop()
        finally:
            timeout.cancel()

    def filter1(self, socket, address, _next):
        return _next('filter1', address)


    def filter2(self, socket, address, _next):
        return _next(socket, 'filter2')


    def test_filters(self):
        server = AsyncServer(('127.0.0.1', 15001), (self.filter1, self.filter2))
        self.assertEquals(server.handle('socket','address'), ('filter1','filter2'))


    def echo(self, socket, address, _next):
        fd = socket.makefile('rb')
        line = fd.readline(1500)
        fd.write(line)
        fd.flush()
        return _next(socket, address)


    def test_raw_connect(self):
        # Create a simple server with a filter than echo's everything written to it
        server = AsyncServer(('127.0.0.1', 15001), (self.echo,))
        server.start()
        socket = self.connect('127.0.0.1', 15001)
        socket.send('hello\n')
        self.assertEquals(socket.recv(6), 'hello\n')
        self.stop(server)


    
