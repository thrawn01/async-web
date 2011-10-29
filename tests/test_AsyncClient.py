#! /usr/bin/env python

from wsgiref.validate import validator
from unittest import TestCase
from urlparse import urlparse
from gevent import monkey
from gevent import socket
from gevent import pywsgi
from StringIO import StringIO
from collections import defaultdict
import gevent
import sys

MAX_HEADER_LENGTH = 1500

class Response(object):
    
    def __init__(self, file, version, status, reason, headers):
        self.version = version
        self.status = status
        self.reason = reason
        self.headers = self._normalizeHeaders(headers)
        self.file = file


    def _normalizeHeaders(self,headers):
        new_headers = {}

        try:
            new_headers['Content-Length'] = int(headers['Content-Length'][0])
            del headers['Content-Length']
        except ValueError:
            raise RuntimeError('Invalid value for "Content-Length" header; %s' % headers['content-length'][0])
        except KeyError:
            # Assume the response is chunked
            pass

        for (key,value) in headers.iteritems():
            if len(value) > 1:
                new_headers[key] = value
                continue
            new_headers[key] = value[0]
        
        return new_headers


    def read(self,length):
        return self.file.read(length)


    def readall(self):
        try:
            return self.file.read(self.headers['Content-Length'])
        except KeyError:
            # XXX Handle chunked read 
            pass


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
        status_line = file.readline(MAX_HEADER_LENGTH + 1)
        # If the status-line is larger than the default MTU, it's bogus
        if len(status_line) > MAX_HEADER_LENGTH:
            raise RuntimeError('Refusing to parse extremely long status_line; %s' % status_line )

        try:
            (http_version, status_code, reason_phrase) = status_line.split(None,2)
        except ValueError:
            raise RuntimeError('Invalid HTTP/1.1 status line in response header; %s' % status_line)
        
        if http_version != 'HTTP/1.1':
            raise RuntimeError('Server responded with unsupported HTTP Version; Only HTTP/1.1 supported')
        
        try:
            # Although RFC2616 Only mentions 100 through 505, Extensions could utilize up to 999
            status_code = int(status_code)
            if status_code < 100 or status_code > 999:
                raise ValueError()
        except ValueError:
            raise RuntimeError("Invalid status code in HTTP/1.1 status line; %s" % status_line)
        
        return (11, status_code, reason_phrase.rstrip())
   

    def _response_header(self, file):
        headers = defaultdict(list)
        while True:
            # Read each header should conform to a format of 'key:value\r\n'
            header_line = file.readline(MAX_HEADER_LENGTH + 1)

            # If the header-line is larger than the default MTU, it's bogus
            length = len(header_line)
            if length > MAX_HEADER_LENGTH:
                raise RuntimeError('Refusing to parse extremely long header line; %s' % header_line )
            if length == 0:
                raise RuntimeError('HTTP header incorrectly terminated; expected "\\r\\n"')

            # Is this the last line in the header?
            if header_line == '\r\n':
                return headers

            try:
                # If the line does not conform to accepted header format, split or the unpack should let us know
                (key, value) = header_line.split(':', 1)
                headers[key.strip()].append(value.strip())
            except ValueError:
                raise RuntimeError('Malformed header found while parsing server response; %s' % header_line)
         
            # XXX If content-length is missing, should we assume chunked? or check for it?


    def _parse_response(self, sock):
        # Use a file like object so we can use readline()
        fd = sock.makefile('fb')
        # Parse the status line 
        (version, status, reason) = self._status_line(fd)
        # Parse the response headers
        headers = self._response_header(fd)
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
        self.application = validator(self.application)
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

    def test_response_header(self):
        client = AsyncClient()
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT\r\n\r\n')

        headers = client._response_header(file)
        self.assertEquals(dict(headers), { 'Content-Type': ['text/plain'],
                                           'Content-Length': ['11'],
                                           'Date': ['Fri, 28 Oct 2011 18:40:19 GMT'] })

    def test_response_raises(self):
        client = AsyncClient()

        # Should raise on long header line ( aka, no \r\n )
        long_header = ''.join([ ' ' for x in range(1,1500) ])
        self.assertRaises(RuntimeError, client._response_header, StringIO(long_header))
        
        # Should raise on incorrectly terminated headers
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT\r\n')
        self.assertRaises(RuntimeError, client._response_header, file)
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT')
        self.assertRaises(RuntimeError, client._response_header, file)
        
        # Should raise on malformed, garbage headers
        file = StringIO('Content-Type- text/plain\r\nContent-LengthASDF#$^@#$G@#G#@%H@#$BH@#$#$')
        self.assertRaises(RuntimeError, client._response_header, file)


    def test_connect(self):
        resp = AsyncClient().get('http://127.0.0.1:15001/')
        print resp.readall()



class TestResponse(TestCase):

    def test_constructor(self):
        resp = Response(StringIO(''), 11, 200, 'OK', {'Content-Length':['11'], 'Param':['1','2']})
        self.assertEquals(resp.headers, {'Content-Length': 11, 'Param':['1','2']} )


