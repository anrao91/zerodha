import os
import cherrypy
import csv
import datetime
import json
import redis
import redisco
import requests
import zipfile
from io import BytesIO, TextIOWrapper
from redisco import models
from time import mktime
from redis import Redis
from rq_scheduler import Scheduler

# from cherrypy.lib.static import serve_file
from jinja2 import Environment, FileSystemLoader

# Class level variables
localDir = os.path.dirname(__file__)
absDir = os.path.join(os.getcwd(), localDir)
current_dir = os.path.dirname(os.path.abspath(__file__))

# Setup some rendering templates
env = Environment(loader=FileSystemLoader(current_dir), trim_blocks=True)

redisco.connection_setup(host='localhost', port=6379, db=10)
POOL = redis.ConnectionPool(host='localhost', port=6379, db=10)
sorted_set_server = redis.Redis(connection_pool=POOL)
scheduler = Scheduler(connection=Redis())


class Stock(models.Model):
    sc_code = models.IntegerField(required=True)
    sc_name = models.Attribute(required=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sc_open = models.FloatField()
    sc_high = models.FloatField()
    sc_low = models.FloatField()
    sc_close = models.FloatField()
    sc_prevclose = models.FloatField()
    sc_volume = models.IntegerField()


# class Parse_Store_Stock(object):
def get_csv_data(file_url):
    cherrypy.log("File name is : {}".format(file_url))
    response = requests.get(file_url, stream=True)
    zip_file = zipfile.ZipFile(BytesIO(response.content))
    zip_names = zip_file.namelist()
    if len(zip_names) == 1:
        file_name = zip_names.pop()
        extracted_file = zip_file.open(file_name)
        csv_data = TextIOWrapper(extracted_file)
        csv_list = list(csv.DictReader(csv_data))
    return csv_list


def get_sc_row(item):
    try:
        sc_code = int(item.get('SC_CODE'))
        sc_name = item.get('SC_NAME')
        sc_open = float(item.get('OPEN'))
        sc_high = float(item.get('HIGH'))
        sc_low = float(item.get('LOW'))
        sc_close = float(item.get('CLOSE'))
        sc_prevclose = float(item.get('PREVCLOSE'))
        sc_volume = int(item.get('NO_OF_SHRS'))
        sc_row = Stock(sc_code=sc_code, sc_name=sc_name, sc_open=sc_open,
                       sc_high=sc_high, sc_low=sc_low, sc_close=sc_close,
                       sc_prevclose=sc_prevclose, sc_volume=sc_volume)
    except Exception:
        cherrypy.log(str(sc_row.errors))
    return sc_row


def store_csv_data():
    # Todo: Remove the timedelta.
    current_date = datetime.date.today() - datetime.timedelta(1)
    current_date = current_date.strftime("%d%m%y")
    csv_list = get_csv_data('http://www.bseindia.com/download/BhavCopy/Equity/EQ' +
                            current_date + '_CSV.ZIP')
    for item in csv_list:
        ''' Store using redisco ORM Model '''
        sc_row = get_sc_row(item)
        if sc_row.is_new():
            sc_row.save()

        ''' Store using native redis sorted sets - for string search / autocomplete'''
        item_set = (item.get("SC_NAME") + ":" + item.get("SC_CODE") + ":" +
                    item.get("OPEN") + ":" + item.get("HIGH") +
                    item.get("LOW") + ":" + item.get("CLOSE"))
        sorted_set_server.zadd("Stocks", item_set, 0)


def get_sc_by_code(code):
    '''
        Returns the Stock values based on the given code
    '''

    result = Stock.objects.filter(sc_code=code).first().attributes_dict
    result['created_at'] = result['created_at'].strftime('%Y-%m-%d')
    return result


def get_sc_by_name_autocomplete(query_string):
    '''
        Returns all possible names for a given query string
    '''
    result_list = []
    search_res = sorted_set_server.zrangebylex("Stocks", "[" + query_string, "[" + query_string + "\\xff")

    for item in search_res:
        temp_list = item.split(":", 2)
        result_list.append({"id": temp_list[1], "label": temp_list[0], "value": temp_list[0]})

    return result_list


def get_top_stock_entries(no_of_entries):
    cherrypy.log(str(Stock.objects.all().order("-sc_high").limit(no_of_entries)))


class Root(object):
    @cherrypy.expose
    def index(self):
        # store_csv_data()
        template_index = env.get_template('index.html')
        return template_index.render()

    def _cp_dispatch(self, vpath):
        if len(vpath) == 1:
            cherrypy.request.params['term'] = vpath.pop()
            return self

        return vpath

    @cherrypy.expose
    @cherrypy.popargs('term')
    @cherrypy.tools.accept(media='text/plain')
    def autocomplete(self, term):
        if term is None:
            return "Not a valid string!"
        result = get_sc_by_name_autocomplete(term.upper())
        return json.dumps(result)

    @cherrypy.expose
    @cherrypy.popargs('term')
    @cherrypy.tools.json_out()
    @cherrypy.tools.accept(media='text/plain')
    def stock_details(self, term):
        if term is None:
            return "Not a valid string!"
        result = get_sc_by_code(term)
        return result


timestamp = mktime(datetime.datetime(2017, 12, 28, 17, 29, 000000).timetuple())
scheduled_time = datetime.datetime.utcfromtimestamp(timestamp)

scheduler.schedule(
    scheduled_time=scheduled_time,  # Time for first execution, in UTC timezone
    func=store_csv_data,  # Function to be queued
    interval=86400,                   # Time before the function is called again, in seconds(here: 1 day)
)


if __name__ == '__main__':
    conf = {
        '/': {
            'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
            'tools.sessions.on': True,
            'tools.response_headers.on': True,
            'tools.response_headers.headers': [('Content-Type', 'text/plain')],
        }
    }

    conf_file = os.path.abspath(os.path.join(os.getcwd(), os.pardir)) + 'dev.conf'

    cherrypy.quickstart(Root(), '/', conf_file)
