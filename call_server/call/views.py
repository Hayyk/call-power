import random
import urlparse

import pystache
import twilio.twiml

from flask import abort, Blueprint, request, url_for, current_app
from flask_jsonpify import jsonify
from twilio import TwilioRestException
from sqlalchemy.exc import SQLAlchemyError

from .models import Call
from ..campaign.models import Campaign, Target
from ..political_data.lookup import locate_targets

call = Blueprint('call', __name__)
call_methods = ['GET', 'POST']


def play_or_say(r, audio, **kwds):
    # take twilio response and play or say message from an AudioRecording
    # can use mustache templates to render keyword arguments

    if audio.file_storage:
        r.play(audio.file_url())
    else:
        msg = pystache.render(audio.text_to_speech, kwds)
        r.say(msg)


def full_url_for(route, **kwds):
    return urlparse.urljoin(current_app.config['APPLICATION_ROOT'],
                            url_for(route, **kwds))


def parse_params(r):
    params = {
        'userPhone': r.values.get('userPhone'),
        'campaignId': r.values.get('campaignId', 0),
        'zipcode': r.values.get('zipcode', None),
    }

    # lookup campaign by ID
    campaign = Campaign.query.get(params['campaignId'])

    if not campaign:
        return None, None

    # get target id by zip code
    if params['zipcode']:
        params['targetIds'] = locate_targets(params['zipcode'])

    return params, campaign


def intro_zip_gather(params, campaign):
    resp = twilio.twiml.Response()

    play_or_say(resp, campaign.audio('msg_intro'))

    return zip_gather(resp, params, campaign)


def zip_gather(resp, params, campaign):
    with resp.gather(numDigits=5, method="POST",
                     action=url_for("zip_parse", **params)) as g:
        play_or_say(g, campaign.audio('msg_ask_zip'))

    return str(resp)


def make_calls(params, campaign):
    """
    Connect a user to a sequence of targets.
    Required params: campaignId, targetIds
    Optional params: zipcode,
    """
    resp = twilio.twiml.Response()

    n_targets = len(params['targetIds'])

    play_or_say(resp, campaign.audio('msg_call_block_intro'),
                n_targets=n_targets, many_reps=n_targets > 1)

    resp.redirect(url_for('make_single_call', call_index=0, **params))

    return str(resp)


@call.route('/make_calls', methods=call_methods)
def _make_calls():
    params, campaign = parse_params(request)

    if not params or not campaign:
        abort(404)

    return make_calls(params, campaign)


@call.route('/create', methods=call_methods)
def create():
    """
    Makes a phone call to a user.
    Required Params:
        userPhone
        campaignId
    Optional Params:
        zipcode
        targetIds
    """
    # parse the info needed to make the call
    params, campaign = parse_params(request)

    if not params or not campaign:
        abort(404)

    # initiate the call
    try:
        call = current_app.config['TWILIO_CLIENT'].calls.create(
            to=params['userPhone'],
            from_=random.choice([n.number for n in campaign.phone_number_set]),
            url=full_url_for("connection", **params),
            timeLimit=current_app.config['TWILIO_TIME_LIMIT'],
            timeout=current_app.config['TWILIO_TIMEOUT'],
            status_callback=full_url_for("call_complete_status", **params))

        result = jsonify(message=call.status, debugMode=current_app.debug)
        result.status_code = 200 if call.status != 'failed' else 500
    except TwilioRestException, err:
        result = jsonify(message=err.msg)
        result.status_code = 200

    return result


@call.route('/connection', methods=call_methods)
def connection():
    """
    Call handler to connect a user with their congress person(s).
    Required Params:
        campaignId
    Optional Params:
        zipcode
        targetIds (if not present go to incoming_call flow and asked for zipcode)
    """
    params, campaign = parse_params(request)

    if not params or not campaign:
        abort(404)

    if params['targetIds']:
        resp = twilio.twiml.Response()

        play_or_say(resp, campaign.audio('msg_intro'))

        action = url_for("_make_calls", **params)

        with resp.gather(numDigits=1, method="POST", timeout=10,
                         action=action) as g:
            play_or_say(g, campaign.audio('msg_intro_confirm'))

            return str(resp)
    else:
        return intro_zip_gather(params, campaign)


@call.route('/incoming_call', methods=call_methods)
def incoming_call():
    """
    Handles incoming calls to the twilio numbers.
    Required Params: campaignId

    Each Twilio phone number needs to be configured to point to:
    server.org/incoming_call?campaignId=12345
    from twilio.com/user/account/phone-numbers/incoming
    """
    params, campaign = parse_params(request)

    if not params or not campaign:
        abort(404)

    return intro_zip_gather(params, campaign)


@call.route("/zip_parse", methods=call_methods)
def zip_parse():
    """
    Handle a zip code entered by the user.
    Required Params: campaignId, Digits
    """
    params, campaign = parse_params(request)

    if not params or not campaign:
        abort(404)

    zipcode = request.values.get('Digits', '')
    target_ids = locate_targets(zipcode)

    if current_app.debug:
        print 'DEBUG: zipcode = {}'.format(zipcode)

    if not target_ids:
        resp = twilio.twiml.Response()
        play_or_say(resp, campaign.audio('msg_invalid_zip'))

        return zip_gather(resp, params, campaign)

    params['zipcode'] = zipcode
    params['targetIds'] = target_ids

    return make_calls(params, campaign)


@call.route('/make_single_call', methods=call_methods)
def make_single_call():
    params, campaign = parse_params(request)

    if not params or not campaign:
        abort(404)

    i = int(request.values.get('call_index', 0))
    params['call_index'] = i
    current_target = Target.query.get(params['targetIds'][i])
    target_phone = str(current_target.number)
    full_name = current_target.full_name()

    resp = twilio.twiml.Response()

    play_or_say(resp, campaign.audio('msg_rep_intro'), name=full_name)

    if current_app.debug:
        print u'DEBUG: Call #{}, {} ({}) from {} in make_single_call()'.format(
            i, full_name, target_phone, params['userPhone'])

    resp.dial(target_phone, callerId=params['userPhone'],
              timeLimit=current_app.config['TWILIO_TIME_LIMIT'],
              timeout=current_app.config['TWILIO_TIMEOUT'], hangupOnStar=True,
              action=url_for('call_complete', **params))

    return str(resp)


@call.route('/call_complete', methods=call_methods)
def call_complete():
    params, campaign = parse_params(request)
    i = int(request.values.get('call_index', 0))

    if not params or not campaign:
        abort(404)

    call_data = {
        'campaign_id': campaign['id'],
        'target_id': params['targetIds'][i],
        'location': params['zipcode'],
        'call_id': request.values.get('CallSid', None),
        'status': request.values.get('DialCallStatus', 'unknown'),
        'duration': request.values.get('DialCallDuration', 0)
    }
    if current_app.config['LOG_PHONE_NUMBERS']:
        call_data['phone_number'] = params['userPhone']

    try:
        current_app.db.session.add(Call(**call_data))
        current_app.db.session.commit()
    except SQLAlchemyError:
        current_app.logger.error('Failed to log call:', exc_info=True)

    resp = twilio.twiml.Response()

    i = int(request.values.get('call_index', 0))

    if i == len(params['targetIds']) - 1:
        # thank you for calling message
        play_or_say(resp, campaign.audio('msg_final_thanks'))
    else:
        # call the next target
        params['call_index'] = i + 1  # increment the call counter

        play_or_say(resp, campaign.audio('msg_between_thanks'))

        resp.redirect(url_for('make_single_call', **params))

    return str(resp)


@call.route('/call_complete_status', methods=call_methods)
def call_complete_status():
    # async callback from twilio on call complete
    params, _ = parse_params(request)

    if not params:
        abort(404)

    return jsonify({
        'phoneNumber': request.values.get('To', ''),
        'callStatus': request.values.get('CallStatus', 'unknown'),
        'targetIds': params['targetIds'],
        'campaignId': params['campaignId']
    })
