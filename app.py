import os
import base64
import binascii
from urllib.parse import unquote
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import phonenumbers

# Load environment variables
load_dotenv()

# Flask setup
app = Flask(__name__, static_folder='static')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configs
CARRIERS = {
    "MTN": {
        "countries": ["ZM"],
        "logo": "/static/mtn-logo.png",
        "prefixes": ["76", "96"]
    },
    "Airtel": {
        "countries": ["ZM"],
        "logo": "/static/airtel-logo.png",
        "prefixes": ["77", "97"]
    },
    "Zamtel": {
        "countries": ["ZM"],
        "logo": "/static/zamtel-logo.png",
        "prefixes": ["95"]
    }
}

UTILITIES = {
    "electricity": {
        "ZESCO": {
            "logo": "/static/zesco-logo.png",
            "account_regex": "^[0-9]{10}$"  # Example pattern
        },
        "Copperbelt Energy Corporation": {
            "logo": "/static/cec-logo.png",
            "account_regex": "^[A-Z]{2}[0-9]{8}$"
        }
    },
    "water": {
        "Lusaka Water and Sewerage Company": {
            "logo": "/static/lwsc-logo.png",
            "account_regex": "^LW[0-9]{8}$"
        },
        "Nkana Water and Sewerage Company": {
            "logo": "/static/nwsc-logo.png",
            "account_regex": "^NW[0-9]{8}$"
        }
    }
}

SCHOOLS = {
    "University of Zambia": {
        "logo": "/static/unza-logo.png",
        "student_id_regex": "^[0-9]{8}$"
    },
    "Copperbelt University": {
        "logo": "/static/cbu-logo.png",
        "student_id_regex": "^CBU[0-9]{5}$"
    },
    "Mulungushi University": {
        "logo": "/static/mu-logo.png",
        "student_id_regex": "^MU[0-9]{6}$"
    }
}

COUNTRIES = {
    "ZM": {"name": "Zambia", "code": "+260"}
}

# Environment variables
LND_REST_URL = os.getenv('LND_REST_URL')
LND_MACAROON = os.getenv('LND_MACAROON')
LND_CERT = os.getenv('LND_CERT')

# Routes
@app.route("/")
def index():
    return render_template('index.html', 
                         countries=COUNTRIES, 
                         carriers=CARRIERS,
                         utilities=UTILITIES,
                         schools=SCHOOLS)

@app.route('/api/btc/balance')
def get_btc_balance():
    try:
        response = requests.get(
            f"{LND_REST_URL}/v1/balance/blockchain",
            headers={'Grpc-Metadata-macaroon': LND_MACAROON},
            verify=LND_CERT if LND_CERT else False
        )
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/btc/create_invoice', methods=['POST'])
def create_btc_invoice():
    try:
        data = request.json

        required_fields = ['amount', 'memo', 'zmw', 'service']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        amount_sats = int(data['amount'])
        usd_amount = float(data['zmw'])
        service_type = data['service']

        # Service-specific validation
        if service_type == 'airtime':
            if 'phone' not in data or 'carrier' not in data:
                return jsonify({'error': 'Missing phone or carrier for airtime'}), 400
        elif service_type == 'electricity':
            if 'utility' not in data or 'account' not in data:
                return jsonify({'error': 'Missing utility or account for electricity'}), 400
        elif service_type == 'water':
            if 'utility' not in data or 'account' not in data:
                return jsonify({'error': 'Missing utility or account for water'}), 400
        elif service_type == 'schoolfees':
            if 'institution' not in data or 'studentId' not in data or 'studentName' not in data:
                return jsonify({'error': 'Missing institution, student ID or name for school fees'}), 400

        invoice_data = {
            'value': amount_sats,
            'memo': data['memo'],
            'expiry': '600'  # 10 minutes
        }

        response = requests.post(
            f"{LND_REST_URL}/v1/invoices",
            headers={
                'Grpc-Metadata-macaroon': LND_MACAROON,
                'Content-Type': 'application/json'
            },
            json=invoice_data,
            verify=LND_CERT if LND_CERT else False
        )

        if response.status_code != 200:
            return jsonify({
                'error': f'LND error: {response.status_code}',
                'details': response.text
            }), 500

        invoice = response.json()
        payment_hash = base64.b64decode(invoice['r_hash']).hex()

        # Here you would typically:
        # 1. Store the payment details in your database
        # 2. Queue a job to process the payment once settled
        # 3. Initiate the bill payment once the invoice is paid

        return jsonify({
            'payment_request': invoice['payment_request'],
            'payment_hash': payment_hash,
            'amount': amount_sats,
            'service': service_type
        })

    except Exception as e:
        return jsonify({'error': str(e), 'message': 'Failed to create invoice'}), 500

@app.route('/api/btc/invoice_status/<path:payment_hash>', methods=['GET'])
def check_invoice_status(payment_hash):
    try:
        lnd_hash = unquote(payment_hash)

        response = requests.get(
            f"{LND_REST_URL}/v1/invoice/{lnd_hash}",
            headers={'Grpc-Metadata-macaroon': LND_MACAROON},
            verify=LND_CERT if LND_CERT else False
        )

        if response.status_code != 200:
            return jsonify({'error': 'Invoice not found in LND', 'details': response.text}), 404

        invoice = response.json()
        return jsonify({
            'settled': invoice['settled'],
            'state': invoice['state'],
            'payment_request': invoice['payment_request']
        })

    except Exception as e:
        return jsonify({'error': str(e), 'message': 'Error checking invoice status'}), 500

@app.route('/api/btc/info')
def get_lnd_info():
    try:
        response = requests.get(
            f"{LND_REST_URL}/v1/getinfo",
            headers={'Grpc-Metadata-macaroon': LND_MACAROON},
            verify=LND_CERT if LND_CERT else False
        )
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@socketio.on('verify_number')
def handle_number_verification(data):
    phone_number = data['phone_number']
    country_code = data['country_code']
    selected_carrier = data['carrier']

    try:
        parsed_number = phonenumbers.parse(phone_number, country_code)
        if not phonenumbers.is_valid_number(parsed_number):
            emit('verification_result', {'valid': False, 'message': 'Invalid phone number'})
            return

        national_number = str(parsed_number.national_number)
        carrier_prefixes = CARRIERS.get(selected_carrier, {}).get('prefixes', [])

        if not any(national_number.startswith(prefix) for prefix in carrier_prefixes):
            emit('verification_result', {
                'valid': False,
                'message': f'Number does not match {selected_carrier} prefixes'
            })
            return

        emit('verification_result', {
            'valid': True,
            'formatted': phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            'carrier': selected_carrier
        })

    except Exception as e:
        emit('verification_result', {'valid': False, 'message': str(e)})

@app.errorhandler(404)
def not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify(error=str(e)), 500

# Entry point
if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)