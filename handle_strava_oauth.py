import json
import os
import time
import requests
import boto3

# ==============================================================================
# FILE 1: handle_strava_oauth.py
# PURPOSE: Handles the OAuth callback from Strava after user authorization.
# TRIGGER: API Gateway (GET request to your /callback endpoint)
# ==============================================================================

def handle_strava_oauth(event, context):
    """
    Exchanges the authorization 'code' from Strava for access/refresh tokens.
    Saves the athlete's information and tokens to DynamoDB.
    """
    print("Received OAuth callback event:", json.dumps(event))

    # --- Get configuration from environment variables ---
    try:
        strava_client_id = os.environ['STRAVA_CLIENT_ID']
        strava_client_secret = os.environ['STRAVA_CLIENT_SECRET']
        dynamodb_table_name = os.environ['DYNAMODB_TABLE_NAME']
    except KeyError as e:
        print(f"ERROR: Missing environment variable: {e}")
        return {'statusCode': 500, 'body': 'Server configuration error.'}

    # --- Extract authorization code from the request ---
    try:
        auth_code = event['queryStringParameters']['code']
    except (KeyError, TypeError):
        print("ERROR: Authorization code not found in request.")
        return {'statusCode': 400, 'body': 'Authorization code is missing.'}

    # --- Exchange auth code for tokens with Strava ---
    token_url = 'https://www.strava.com/api/v3/oauth/token'
    payload = {
        'client_id': strava_client_id,
        'client_secret': strava_client_secret,
        'code': auth_code,
        'grant_type': 'authorization_code'
    }

    try:
        response = requests.post(token_url, data=payload)
        response.raise_for_status()  # Raises an exception for 4XX/5XX status codes
        token_data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to exchange token with Strava. Response: {e.response.text}")
        return {'statusCode': 502, 'body': 'Failed to communicate with Strava.'}

    # --- Save athlete info and tokens to DynamoDB ---
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(dynamodb_table_name)
        
        athlete_info = token_data['athlete']
        
        # The athlete's ID is the perfect primary key for our table.
        athlete_id = athlete_info['id']

        # Prepare the item for DynamoDB
        db_item = {
            'athlete_id': athlete_id,
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': token_data['expires_at'],
            'firstname': athlete_info.get('firstname', ''),
            'lastname': athlete_info.get('lastname', ''),
            'profile_picture': athlete_info.get('profile', ''),
            'last_updated': int(time.time())
        }

        table.put_item(Item=db_item)
        print(f"Successfully saved token for athlete {athlete_id}")

    except Exception as e:
        print(f"ERROR: Failed to save data to DynamoDB: {e}")
        # This is a critical error, as we have the token but can't save it.
        return {'statusCode': 500, 'body': 'Failed to save user data.'}
        
    # --- Return a success page to the user ---
    # This simple HTML page will be shown in the user's browser after auth.
    success_html = """
    <html>
        <head><title>Success</title><style>body{font-family:sans-serif;text-align:center;padding-top:50px;}</style></head>
        <body>
            <h1>Authorization Successful!</h1>
            <p>You can now close this window. Your new activities will be analyzed automatically.</p>
        </body>
    </html>
    """
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/html'},
        'body': success_html
    }
