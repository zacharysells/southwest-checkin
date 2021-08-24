import sys
import time
import re
import docker
import logging
logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse

class InvalidCheckinString(Exception):
    pass

class FailedCheckin(Exception):
    pass

class DuplicateCheckin(Exception):
    pass

class UnauthorizedNumber(Exception):
    pass

app = FastAPI()
client = docker.from_env()

@app.post("/hook")
async def chat(From: str = Form(...), Body: str = Form(...)):
    logging.info('Message came in from %s: %s' % (From, Body))
    response = MessagingResponse()
    try:
        resp = process_message(Body, From)
        response.message(resp)
    except InvalidCheckinString as e:
        response.message(str(e))
    except FailedCheckin as e:
        response.message(str(e))
    except DuplicateCheckin as e:
        response.message(str(e))
    except UnauthorizedNumber as e:
        # Intentionally not responding
        return None
    except Exception as e:
        logging.error(str(e))
        response.message("Something went wrong. Please try again or contact the administrator")
    finally:
        return Response(content=str(response), media_type="application/xml")

def validate_checkin_string(msg):
    pattern = re.compile("^[a-zA-Z0-9_]{6}\s\w*\s\w*\s*$")
    return bool(pattern.search(msg))

def validate_duplicate_checkin(msg):
    if msg.lower().strip() in [' '.join(con.attrs['Config']['Cmd']).lower().strip() for con in client.containers.list()]:
        return False
    else:
        return True

def validate_successful_schedule(con):
    con = client.containers.get(con.id) # We need to "get" container again to get current state
    logging.info('State of container %s - %s' % (con.id[:12], con.attrs['State']['Status']))
    if con.attrs['State']['Running']:
        if b'Flight information found' in con.logs():
            return True
        else:
            logging.warn('Couldnt find reservation. Container logs below')
            logging.warn(con.logs())
    return False

def authenticate_user(number, admin=False):
    if admin:
        whitelist_file = 'admin_whitelist.txt'
    else:
        whitelist_file = 'user_whitelist.txt'
    try:
        with open(whitelist_file, 'r') as fp:
            lines = fp.read().splitlines()
    except IOError:
        # If whitelist file doesn't exist, default to allow all
        return True

    if number not in lines:
        logging.warn("Unauthorized attempt by %s" % number)
        raise UnauthorizedNumber


def schedule_checkin(msg):
    logging.info('Attempting scheduled checkin for %s' % msg)
    if not validate_checkin_string(msg):
        logging.warn('Invalid checkin string.')
        raise InvalidCheckinString("Checkin message should be of form: CONFIRMATION_NUMBER FIRST_NAME LAST_NAME")
    if not validate_duplicate_checkin(msg):
        logging.warn('Duplicate check-in detected.')
        raise DuplicateCheckin("This reservation has already had a check-in scheduled.")
    
    con = client.containers.run("pyro2927/southwestcheckin:latest", detach=True, command=msg)
    time.sleep(3)
    if validate_successful_schedule(con):
        return con.id[:12]
    else:
        raise FailedCheckin("Unable to find reservation. Please double check check-in string.")

def process_message(msg, number):
    if msg.lower().strip().startswith("logs"):
        authenticate_user(number, admin=True)
        con = client.containers.get(msg.lower().split()[1])
        return con.logs().decode("utf-8")
    elif msg.lower().strip() == "ls":
        authenticate_user(number, admin=True)
        return '\n'.join(['%s %s' % (con.id[:12], ' '.join(con.attrs['Config']['Cmd'] if con.attrs['Config']['Cmd'] else [])) for con in client.containers.list()])
    else:
        authenticate_user(number)
        return 'Check-in successful! Check-in ID: %s' % schedule_checkin(msg)

if __name__ == "__main__":
    print(process_message(sys.argv[1], 'nothing'))