#! /usr/bin/env python

class Demo():

    def get(self, request, response, _next):
        response.send("Hello,World")

    def post(self, request, response, _next):
        # Params() is a class that parses URL encoded values into a multimap
        post = Params(request.read())
        # Echo the posted value param1
        response.send(post.param1)

demo = Demo()



routes = Routes()
# On GET and match '/' then call demo.get(self,request,response, _next)
routes.get('/', demo, demo.get)
# On POST and match '/' then call demo.post(self,request,response, _next)
routes.post('/', demo, demo.post)


# Instaniate the main App
app = AsyncServer()

# Require oAuth on all urls
app.add(OAuth(('/'))

# Encode all data in JSON - This filter replaces the 'send' method on request object for every request that matches the URL path '/'
app.add(JSON(('/')))

app.add(routes)


app.startServer(8080)


# With class decorators and filters declared in the constructor
AsyncServer( (OAuth('/'), JSON('/'), Routes(Demo()) ) ).startServer(8080)


# Http Client 
class Client():
   
    @client('GET', url='http://www.google.com', filters=())
    def getGoogle(request, response):
        # Prints out the google page
        print request.read()
    
    def returnResp(request, response):
        return request

req = AsyncClient().get(url='http://www.google.com', filters=(Auth('user','pass'), self=Client(), method=Client.returnResp)
# if no call back is defined, defaults to returning the 'request' object
req = AsyncClient().get(url='http://www.google.com', filters=(Auth('user','pass')))
print req.headers
print req.recv()



