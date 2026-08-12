"""Fake backend for local end-to-end testing ONLY. Logs and stores
whatever callbacks the two nodes send it, so the async submit -> queue ->
callback flow can be verified with plain curl before a real backend
exists. Not a stand-in for the eventual queue-dispatch backend (see the
prompt this was built from) -- just an inspection point."""
import logging
import time

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('fake-backend')

app = Flask(__name__)
received = []  # in-memory, dev-only -- fine, this container is disposable


@app.post('/callback-test')
def callback_test():
    payload = request.get_json(force=True)
    payload['_ricevuto_alle'] = time.time()
    received.append(payload)
    log.info('callback ricevuta: %s', payload)
    return jsonify({'ok': True}), 200


@app.get('/callback-test')
def list_received():
    return jsonify(received)


@app.get('/callback-test/<request_id>')
def list_received_for(request_id):
    matches = [p for p in received if p.get('request_id') == request_id]
    return jsonify(matches)


@app.get('/salute')
def salute():
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)
