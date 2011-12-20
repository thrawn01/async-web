#! /usr/bin/env python

from urlparse import urlparse
from gevent.server import StreamServer
import hashlib, base64, random, traceback, time, sys, os, struct
from gevent import socket
from asyncweb.websocket import WEBSOCKET_GUID, Frame


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


def parse_uri(uri):
    url = urlparse(uri)
    port = int(url.port or 80)
    path = url.path or '/'
    scheme = url[0]
    
    if scheme == '':
        raise RuntimeError("Failed to parse malformed URL")

    return (scheme, url.hostname, port, path)


def parse_request_line(file):
    # Read the first line of the request ( should be the status-line )
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


def parse_response_line(file, debug=None):
    # Read the first line of the response ( should be the status-line )
    line = file.readline(MAX_LINE_LENGTH + 1)
    # If the status-line is to large, it's bogus
    if len(line) > MAX_LINE_LENGTH:
        raise RuntimeError('Refusing to parse extremely long status-line; %s' % line )
    
    if debug: debug.write('-- status: "%s"' % line)

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


def parse_header(file, debug=None):
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
        
        if debug: debug.write('-- header: %s' % header_line)

        # Is this the last line in the header?
        if header_line == '\r\n':
            return headers

        try:
            # If the line does not conform to accepted header format, split or the unpack should let us know
            (key, value) = header_line.split(':', 1)
        except ValueError:
            raise HTTPException(400, 'Malformed header found; %s' % header_line)
        
        # Strip the white space around the key and value
        key = key.strip().lower(); value = value.strip()

        # This doesn't seam performant but is. See Commit 3fa292cf and 38b8c18e8
        if key in headers:
            if isinstance(headers[key], list):
                headers[key].append(value)
            else:
                headers[key] = [headers[key], value]
        else:
            headers[key] = value
        
        try:
            headers['content-length'] = int(headers['content-length'])
        except ValueError:
            raise HTTPException(400, 'Invalid value for "content-length" header; %s' % headers['content-length'][0])
        except KeyError:
            # Missing content-length is ok, it might be a chunked response
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


def compose_request_header(method, path='/', headers={}, version='HTTP/1.1'):
    headers = ["%s: %s" % item for item in headers.items()]
    return "%s %s %s\r\n%s\r\n\r\n" % (method, path, version,"\r\n".join(headers))


def capitalize(item):
    capitalized_key = '-'.join([char.capitalize() for char in item[0].split('-')])
    return (capitalized_key, item[1])
    

def compose_response_header(version, status, reason, headers, _time=time.time()):
    if version != 11:
        raise RuntimeError("Response version is not HTTP/1.1; Only HTTP/1.1 is supported")

    if 'Date' not in headers:
        headers['Date'] = http_date_time(_time)

    headers = ["%s: %s" % capitalize(item) for item in headers.items()]
    print "------- HTTP/1.1 %s %s\r\n%s\r\n\r\n" % (status, reason,"\r\n".join(headers))
    return "HTTP/1.1 %s %s\r\n%s\r\n\r\n" % (status, reason,"\r\n".join(headers))


def log_error(msg):
    sys.stderr.write(msg + '\n')


def log_access():
    pass



class HTTPException(RuntimeError):
    
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason


class AsyncServer(StreamServer):

    def __init__(self, listener, filters=None, config=None):
        StreamServer.__init__(self, listener)
        self._filters = filters


    def _next(self,request, response, count):
        if count < len(self._filters):
            return self._filters[count](request, response, lambda req, resp: self._next(req, resp, count+1))
        return (request, response) 


    def handle(self, sock, address):
        # Use a file like object so we can use readline()
        fd = sock.makefile('fb')
        # Create the Response Object
        response = Response(fd, {}, version=11, status=200, reason='OK')
        try:
            # TODO: Refactor this like the 'Frame' class ????

            # Parse the request line 
            (method, path, version) = parse_request_line(fd)
            # Parse the response headers
            headers = parse_header(fd)
            # Create the Request Object
            request = Request(fd, headers, method=method, path=path, version=version, address=address)
            # Pass the Request and Response objects to the next filter on the chain
            self._next(request, response, 0)

        except HTTPException, e:
            self.errors(response, e.status, e.reason)
        except:
            # Dump a stack trace to the log
            log_error(traceback.format_exc())
            # Inform the client of the error
            self.errors(response, 500, "Internal Server Error")
            # XXX: Close the connection 
            try:
                sock._sock.close()  # do not rely on garbage collection
                sock.close()
            except socket.error:
                pass


    def errors(self, response, status, reason):
        # XXX: log.audit() - log all errors to the audit log?
        log_error("Code: %s Reason: %s" % (status, reason))

        # All responses have a reason and a status
        response.set(status=status, reason=reason)

        if status == 405:
            response.headers = { 'Allow': ','.join(SUPPORTED_METHODS) }
        
        response._write('')


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


    def _read(self, callback=None):
        # XXX: Check for values to read, don't want to block if the user calls us again
        # XXX: Remove callback here

        try:
            # RFC2616 Sec 4.4
            # If a Transfer-Encoding header field (section 14.41) is present and has any value other than "identity", 
            # then the transfer-length is defined by use of the "chunked" transfer-coding (section 3.6), 
            # unless the message is terminated by closing the connection. 
            if self.headers['transfer-encoding'] != 'identity':
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
                    # XXX: Remove this call back, just return the data for each chunk, add a method call readall()
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
            length = self.headers['content-length']
            # Server might respond with content-length of 0
            if length <= 0:
                raise KeyError()
            return self.file.read(length)
        except KeyError:
            # If the status was not 200, then it was an HTTP error with no payload
            if self.status != 200:
                raise HTTPException(self.status, self.reason)
            # Else the server responded incorrectly
            raise RuntimeError("Unable to read response; missing 'content-length' header or 'content-length' is 0 and 'Transfer-Encoding' is not 'chunked'")

        # XXX: Check for 'Connection: close' header if it exists, close the connection here


    def _write(self, data):

        # Un-Chunked response
        length = len(data)
        if length != 0:
            self.headers['content-length'] = length
            # Alway default to text/plain if Content-Type not specified
            if 'Content-Type' not in self.headers:
                self.headers['Content-Type'] = 'text/plain'

        # Compose the response
        return self.file.write(compose_response_header(self.version, self.status, self.reason, self.headers))
    
    # Preserve the original read/write
    read = _read
    write =_write


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


    def _parse_server_response(self, sock):
        # Use a file like object so we can use readline()
        fd = sock.makefile('fb')
        # Parse the status line 
        (version, status, reason) = parse_response_line(fd, sys.stderr)
        # Parse the response headers
        headers = parse_header(fd, sys.stderr)
        # Instanciate the user object Response()
        return Response(fd, headers, http_version=version, status=status, reason=reason)

        
    def get(self, uri, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        # Parse the URI
        (scheme, host, port, path) = parse_uri(uri)
        if 'http' != scheme:
            raise RuntimeError("Only http:// Urls supported")

        # Connect to the remote Host
        sock = socket.create_connection((host, port), timeout=timeout)
        # Send the request header
        sock.send(compose_request_header('GET', path, { 'Host': host }))
        # Parse the response and return a Request Object      
        return self._parse_server_response(sock)


    def websocket(self, uri, origin=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        # Parse the URI
        (scheme, host, port, path) = parse_uri(uri)
        #if 'ws' != scheme:
            #raise RuntimeError("Only ws:// Urls supported")

        if origin == None:
            origin = '%s://%s:%s' % (scheme, host, port)

        # Connect to the remote Host
        sock = socket.create_connection((host, port), timeout=timeout)
        # Generate the key/nonce used in handshake 
        key = base64.b64encode(os.urandom(16))

        # WebSocket Headers
        headers = {
            'Host': host,
            'Upgrade': 'websocket',
            'Connection': 'Upgrade',
            'Origin': origin,
            'Sec-WebSocket-Version': '13',
            'Sec-WebSocket-Protocol': 'chat',
            'Sec-WebSocket-Key': key,
        }

        # Send the request header that starts the handshake
        sock.send(compose_request_header('GET', path, headers))
        # Parse the response
        response = self._parse_server_response(sock)

        # Did the server respond with 101?
        if response.status != 101:
            # XXX Handle redirects 3xx responses
            raise HTTPException(response.status, 'Server responded with un-expected status; expected 101')
        

        # XXX Scrub the incoming KEY for possible attacks against b64encode() or sha1()

        # From draft-ietf-hybi-thewebsocketprotocol-17 Section-1.3
        # Concat the key and the GUID, then SHA-1 Hash the Concat, then Base 64 Encode
        expected_key = base64.b64encode(hashlib.sha1(key + WEBSOCKET_GUID).digest())
        headers = response.headers
        try:
            # XXX Did the server respond with a protocol we didn't ask for?

            if headers['upgrade'].lower() != 'websocket':
                raise RuntimeError('Update has invalid value; expected "websocket" got "%s"' % headers['upgrade'])
            if headers['connection'].lower() != 'upgrade':
                raise RuntimeError('Connection has invalid value; expected "upgrade" got "%s"' % headers['connection'])
            if headers['sec-websocket-accept'] != expected_key:
                raise RuntimeError('Sec-WebSocket-Accept has invalid value; expected "%s" got "%s"' % (expected_key, headers['sec-websocket-accept']))
        except KeyError:
            raise RuntimeError('Missing headers in response; expected (upgrade, connection, sec-websocket-accept) got (%s)' % ','.join(headers.keys()))
             
        _read = response.read 
        response.read = lambda : self.read(response.file.read)
        return response


    def read(self, read):
        # Parse the frame into a tuple
        frame = Frame.parseFrame(read)
        # Return the data in the frame
        return frame


