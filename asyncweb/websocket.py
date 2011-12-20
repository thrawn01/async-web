from collections import namedtuple
import hashlib, base64, random, struct

WEBSOCKET_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

# Create a Named Tuple (Class)
   
class Frame(namedtuple('Frame', ['data', 'header'])):
    """ 
        data        is a String
        header      is a 16bit integer
    """

    @staticmethod
    def parseFrame(read):
        # Read the 2 byte frame header
        header = struct.unpack_from('!H', read(2))
        # Last 7 bits of the header consitute the length
        length = header & 0x7F

        if length == 126:
            # If the length is 126, unpack the next 2 bytes
            length = struct.unpack_from('!H', read(2))

        if length == 127:
            # If the length is 127, unpack the next 8 bytes
            length = struct.unpack_from('!L', read(8))

        # Read in the entire frame
        return Frame(read(length), header)


    @staticmethod
    def createFrame(data):
        (header, length) = (0, len(data))

        # XXX: Add addtional flags to the header here
        return Frame(data, header)


    def compose(self):
        length = len(self.data)
        extended = ''

        # XXX: This code assumes only 64-bit integers are possible on this platform
        if length > 65535:
            # Pack length as a 64-bit unsigned
            extended = struct.pack("!Q", length)
            header = self.header | 127

        elif length >= 126:
            # Pack length as a 16-bit unsigned
            extended = struct.pack("!H", length)
            header = self.header | 126
        else:
            header = self.header | length

        return ''.join((struct.pack("!H", header), extended, self.data))



class WebSocket(object):
    """ Websocket filter for hybi draft websocket protocol """
   
    def __init__(self):
        self.routes = {}


    # XXX Add real path route handling
    def add(self, path, handler):
        self.routes[path] = handler


    def valid_origin(self, origin):
        # XXX: Fix me
        return True


    def parse_handshake(self, headers):
        protocols = []
        extensions = []

        # draft-ietf-hybi-thewebsocketprotocol-17 Section 4.2.2
        # Extensions and Protocols can be empty or non-existant
        
        if 'sec-websocket-protocol' in headers:
            protocols = [protocol.strip() for protocol in headers["sec-websocket-protocol"].split(',')]
        if 'sec-websocket-extensions' in headers:
            extensions = [extension.strip() for extension in headers["sec-websocket-extensions"].split(',')]

        try:
            # Validate the correct version is requested
            if headers['sec-websocket-version'].strip() != '13':
                # XXX Respond with a list of acceptable versions 'Sec-WebSocket-Version: 8, 7'
                raise HTTPException(426, 'Server only supports WebSocket Version 13')
           
            # XXX Scrub the incoming KEY for possible attacks against b64encode() or sha1()
            key = headers['sec-websocket-key']
            if len(key) != 24:
                raise HTTPException(403, 'Invalid base64 encoding for Sec-WebSocket-Key')

            return (key, protocols, extensions, headers['origin'].strip())

        except KeyError:
            raise HTTPException(400, 'Incomplete or Malformed Websocket handshake')


    # XXX: Catch all our errors here, do not allow exceptions to fall through for websockets
    def __call__(self, request, response, _next):
        # Validate and parse the handshake
        (key, protocols, extensions, origin) = self.parse_handshake(request.headers)

        # From draft-ietf-hybi-thewebsocketprotocol-17 Section-1.3
        # Concat the key and the GUID, then SHA-1 Hash the Concat, then Base 64 Encode
        key = base64.b64encode(hashlib.sha1(key + WEBSOCKET_GUID).digest())
       
        # Set our return code and reason
        response.set(status = 101, reason = 'Switching Protocols')
        response.headers = {
            'Upgrade': 'websocket',
            'Connection': 'Upgrade',
            'Sec-WebSocket-Accept': key
        }
        # Do this so we don't have a partial handshake, as the handler might 
        # wait for requests instead of issuing a response
        response.write('')
        #_write = response.write 
        
        #print "saved: ", _write
        #print "current: ", response.write
        #print "========="
        ## Replace write with our version of write
        #response.write = lambda data: self.write(data, _write)
#
        #print "saved: ", _write
        #print "current: ", response.write
#
        ## Match the path with a handler, and start handling websocket messages
        #self.routes[request.path](request, response)
        return _next(request, response)


    def read(self, read):
        # Parse the frame into a tuple
        frame = Frame.parseFrame(read)
        # Return the data in the frame
        return frame


    def write(self, data, write):
        # Create a frame as a tuple
        frame = Frame.createFrame(data)
        # Join the tuple and write the result
        write(frame.compose()) 

