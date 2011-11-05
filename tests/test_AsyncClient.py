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
import time
import sys

MAX_LINE_LENGTH = 8192
SUPPORTED_METHODS = ('GET', 'POST', 'PUT', 'DELETE', 'TRACE', 'OPTIONS', 'HEAD')

# Weekday and month names for HTTP date/time formatting
WEEK_DAY_NAME = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_NAME = [None,  # Dummy so we can use 1-based month numbers
              "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
def http_date_time(timestamp):
    year, month, day, hh, mm, ss, wd, _y, _z = time.gmtime(timestamp)
    return "%s, %02d %3s %4d %02d:%02d:%02d GMT" % (WEEK_DAY_NAME[wd], day, MONTH_NAME[month], year, hh, mm, ss)


def parse_header(file):
    headers = {}
    while True:
        # Read each header should conform to a format of 'key:value\r\n'
        header_line = file.readline(MAX_LINE_LENGTH + 1)

        # If the status-line is to large, it's bogus
        length = len(header_line)
        if length > MAX_LINE_LENGTH:
            raise HTTPException(400, 'Refusing to parse extremely long header line; %s' % header_line )
        if length == 0:
            raise HTTPException(400, 'HTTP header incorrectly terminated; expected "\\r\\n"')

        # Is this the last line in the header?
        if header_line == '\r\n':
            return headers

        try:
            # If the line does not conform to accepted header format, split or the unpack should let us know
            (key, value) = header_line.split(':', 1)
        except ValueError:
            raise HTTPException(400, 'Malformed header found; %s' % header_line)
        
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
        
        try:
            headers['Content-Length'] = int(headers['Content-Length'])
        except ValueError:
            raise HTTPException(400, 'Invalid value for "Content-Length" header; %s' % headers['content-length'][0])
        except KeyError:
            # Missing Content-Length is ok, it might be a chunked response
            pass

    return headers


def parse_chunk_header(file):
    # Read the chunk header line
    line = file.readline(MAX_LINE_LENGTH + 1)
    # If the status-line is to large, it's bogus
    if len(line) > MAX_LINE_LENGTH:
        raise RuntimeError('Refusing to parse extremely long chunk-header; %s' % line )

    # Split out the header and the extension if it exists
    chunk_header = line.split(';')
    try:
        # Interpret the length in HEX
        chunk_header[0] = int(chunk_header[0], 16)
    except KeyError, ValueError:
        raise RuntimeError("Expected hexdecimal length while reading chunk header; %s" % line)
   
    return chunk_header


def compose_status_line(version, status, reason):
    if version != 11:
        raise RuntimeError("Response version is not HTTP/1.1; Only HTTP/1.1 is supported")
    return ['HTTP/1.1 %s %s\r\n' % (status, reason),]


def compose_header(headers, _time=time.time()):
    if 'Date' not in headers:
        headers['Date'] = http_date_time(_time)

    return ['\r\n'.join(['%s: %s' % item for item in headers.items()]), '\r\n\r\n']


class HTTPException(RuntimeError):
    
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason


class AsyncServer(StreamServer):

    def __init__(self, listener, filters=None, config=None):
        StreamServer.__init__(self, listener)
        self._filters = filters


    def _next(self,request, response, count):
        if len(self._filters) > count:
            return self._filters[count](socket,address,lambda socket, address: self._next(request, response, count+1))
        return (request, response) 


    def _status_line(self, file):
        # Read the first line of the response ( should be the status-line )
        line = file.readline(MAX_LINE_LENGTH + 1)
        # If the status-line is to large, it's bogus
        if len(line) > MAX_LINE_LENGTH:
            raise RuntimeError('Refusing to parse extremely long status-line; %s' % line )

        try:
            # If the line does not conform to accepted status line, split or the unpack should let us know
            (method, path, http_version) = line.split(None,2)
        except ValueError:
            raise RuntimeError('Invalid HTTP/1.1 status line in request header; %s' % line)
        
        if not http_version.startswith('HTTP/1.1'):
            raise RuntimeError('Client requested unsupported HTTP Version; Only HTTP/1.1 supported')
        
        if method not in SUPPORTED_METHODS:
            raise HTTPException(405, "Client requested un-supported method in HTTP/1.1 status line; %s" % line)

        return (method, path, 11)
        

    def handle(self, sock, address):
        # Use a file like object so we can use readline()
        fd = sock.makefile('fb')
        # Create the Response Object
        response = Response(fd, {}, version=11, status=200, reason='OK')
        try:
            # Parse the status line 
            (method, path, version) = self._status_line(fd)
            # Parse the response headers
            headers = parse_header(fd)
            # Create the Request Object
            request = Request(fd, headers, method=method, path=path, version=version, address=address)
            # Pass the Request and Response objects to the next filter on the chain
            self._next(request, response, 0)

        except HTTPException, e:
            self.errors(response, e.status, e.reason)
        except:
            # XXX: Dump a stack trace to the log
            self.errors(response, 500, "Internal Server Error")

    def errors(self, response, status, reason):
        print "Error - Code: %s Reason: %s" % (status, reason)
        response.set(status=status, reason=reason)
        # XXX: log.audit() - log all errors to the audit log?
        if status == 405:
            response.headers = { 'Allow': ','.join(SUPPORTED_METHODS) }
        
        response.write('')    
        pass



class HTTPSocket(object):

    def __init__(self, file, headers, **attrs):
        self.headers = headers
        self.set(**attrs)
        self.file = file


    def toList(self):
        return list(self.__dict__)


    def __iter__(self, key):
        return self.__dict__


    def set(self, **attrs):
        for key,value in attrs.items():
            setattr(self, key, value)


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
                    chunked_header = parse_chunk_header(self.file)
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
            # If the status was not 200, then it was an HTTP error with no payload
            if self.status != 200:
                raise HTTPException(self.status, self.reason)
            # Else the server responded incorrectly
            raise RuntimeError("Unable to read response; missing 'Content-Length' header and 'Transfer-Encoding' is not 'chunked'")

        # XXX: Check for 'Connection: close' header if it exists, close the connection here



    def write(self, data):

        # Un-Chunked response
        length = len(data)
        if length != 0:
            self.headers['Content-Length'] = length
            # Alway default to text/plain if Content-Type not specified
            if 'Content-Type' not in headers:
                self.headers['Content-Type'] = 'text/plain'

        # Compose the response
        payload = compose_status_line(self.version, self.status, self.reason)
        payload.extend(compose_header(self.headers))
        payload.append(data)
        print "Payload ", payload
        print "Payload ", ''.join(payload)
        return self.file.write(''.join(payload))


class Request(HTTPSocket):
    
    def __init__(self, file, headers, **attrs):
        HTTPSocket.__init__(self, file, headers, **attrs)



class Response(HTTPSocket):
    
    def __init__(self, file, headers, **attrs):
        HTTPSocket.__init__(self, file, headers, **attrs)


    
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

        if 'http' not in scheme:
            raise RuntimeError("Only (http,https) supported")

        return (scheme, url.hostname, port, path)


    def _request_header(self, method, path='/', headers=(), version='HTTP/1.1'):
        headers = [": ".join(x) for x in headers]
        return "%s %s %s\r\n%s\r\n\r\n" % (method, path, version,"\r\n".join(headers))


    def _status_line(self, file):
        # Read the first line of the response ( should be the status-line )
        line = file.readline(MAX_LINE_LENGTH + 1)
        # If the status-line is to large, it's bogus
        if len(line) > MAX_LINE_LENGTH:
            raise RuntimeError('Refusing to parse extremely long status-line; %s' % line )

        try:
            # If the line does not conform to accepted status line, split or the unpack should let us know
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
        return Response(fd, headers, http_version=version, status=status, reason=reason)

        
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
                                           'Content-Length': 11,
                                           'Date': 'Fri, 28 Oct 2011 18:40:19 GMT' })
    
    def test_create_header(self):
        header = compose_header({ 'Always': 'GET,HEAD,POST' }, _time=1320269615.928314)
        result = [ "Date: Wed, 02 Nov 2011 21:33:35 GMT\r\n" \
                 "Always: GET,HEAD,POST\r\n" \
                 "Content-Type: text/plain","\r\n\r\n"]
        self.assertEquals(header, result)

    def test_parse_header_raises(self):

        # Should raise on long header line ( aka, no \r\n )
        long_header = ''.join([ ' ' for x in range(1,1500) ])
        self.assertRaises(HTTPException, parse_header, StringIO(long_header))
        
        # Should raise on incorrectly terminated headers
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT\r\n')
        self.assertRaises(HTTPException, parse_header, file)
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT')
        self.assertRaises(HTTPException, parse_header, file)
        
        # Should raise on malformed, garbage headers
        file = StringIO('Content-Type- text/plain\r\nContent-LengthASDF#$^@#$G@#G#@%H@#$BH@#$#$')
        self.assertRaises(HTTPException, parse_header, file)


    def test_connect(self):
        resp = AsyncClient().get('http://127.0.0.1:15001/')
        print resp.read()



class TestResponse(TestCase):

    def test_constructor(self):
        resp = Response(StringIO(''), {'Content-Length': 11, 'Param':['1','2']}, http_version=11, status=200, reason='OK')
        self.assertEquals(resp.headers, {'Content-Length': 11, 'Param':['1','2']} )
        self.assertEquals(resp.http_version, 11)
        self.assertEquals(resp.status, 200)
        self.assertEquals(resp.reason, 'OK')


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


    #def test_filters(self):
        #server = AsyncServer(('127.0.0.1', 15001), (self.filter1, self.filter2))
        #self.assertEquals(server.handle('socket','address'), ('filter1','filter2'))


    def routes(self, request, response, _next):
        response.write("hello world")
        return _next(socket, address)


    def test_http_connect(self):
        ## Create a simple server with a filter that always responds with 'hello world'
        server = AsyncServer(('127.0.0.1', 15001), (self.routes,))
        server.start()

        resp = AsyncClient().get('http://127.0.0.1:15001/')
        print resp.read()
        self.stop(server)

