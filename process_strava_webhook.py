import json
import os
import time
import requests
import boto3
from decimal import Decimal

# ==============================================================================
# process_strava_webhook.py
# PURPOSE: Analyzes a new activity using Bedrock and updates it on Strava.
# TRIGGER: from a webhook event sent by Strava to API Gateway
# ==============================================================================

def process_strava_webhook(event, context):
    print("Received webhook event:", json.dumps(event))
    try:
        # --- FIX: Parse the payload from the 'body' of the API Gateway event ---
        if 'body' not in event or event['body'] is None:
             raise ValueError("Event body is missing")
        strava_payload = json.loads(event['body'])
        
        # --- End of FIX ---

        if strava_payload.get('aspect_type') != 'create':
            print(f"Ignoring non-create event: {strava_payload.get('aspect_type')}")
            return {'statusCode': 200, 'body': json.dumps('Event ignored (not a create event).')}
        
        athlete_id = strava_payload['owner_id']
        activity_id = strava_payload['object_id']
        
        users_table_name = os.environ['DYNAMODB_TABLE_NAME']
        workouts_table_name = os.environ['DYNAMODB_WORKOUTS_TABLE']
        strava_client_id = os.environ['STRAVA_CLIENT_ID']
        strava_client_secret = os.environ['STRAVA_CLIENT_SECRET']

    except (KeyError, TypeError, json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: Malformed event payload or missing data. Details: {e}")
        return {'statusCode': 400, 'body': json.dumps('Malformed event payload.')}

    # --- Get user's tokens from DynamoDB ---
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(users_table_name)
        response = table.get_item(Key={'athlete_id': athlete_id})
        user_data = response.get('Item')
        if not user_data:
            raise ValueError(f"No user data found for athlete_id {athlete_id}")
    except Exception as e:
        print(f"ERROR: DynamoDB get_item failed: {e}")
        return {'statusCode': 500, 'body': json.dumps('Could not retrieve user data.')}

    # --- Check if token is expired and refresh if necessary ---
    access_token = user_data['access_token']
    if user_data['expires_at'] < time.time():
        print("Token expired, refreshing...")
        try:
            res = requests.post('https://www.strava.com/api/v3/oauth/token', data={
                'client_id': strava_client_id,
                'client_secret': strava_client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': user_data['refresh_token']
            })
            res.raise_for_status()
            new_token_data = res.json()
            table.update_item(
                Key={'athlete_id': athlete_id},
                UpdateExpression="set access_token=:a, refresh_token=:r, expires_at=:e",
                ExpressionAttributeValues={
                    ':a': new_token_data['access_token'], ':r': new_token_data['refresh_token'], ':e': new_token_data['expires_at']
                }
            )
            access_token = new_token_data['access_token']
            print("Token refreshed and updated in DB.")
        except Exception as e:
            print(f"ERROR: Could not refresh token: {e}")
            return {'statusCode': 500, 'body': json.dumps('Failed to refresh Strava token.')}
    
    # --- Fetch the new activity details from Strava ---
    activity_url = f'https://www.strava.com/api/v3/activities/{activity_id}'
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        res = requests.get(activity_url, headers=headers)
        res.raise_for_status()
        activity_data = res.json()
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to fetch activity {activity_id} from Strava. Response: {e.response.text if e.response else 'No response'}")
        return {'statusCode': 502, 'body': json.dumps('Could not fetch activity from Strava.')}

    # --- Prepare data for Bedrock ---
    distance_km = activity_data.get('distance', 0) / 1000
    moving_time_min = activity_data.get('moving_time', 0) / 60
    avg_pace_min_km = moving_time_min / distance_km if distance_km > 0 else 0
    avg_hr = activity_data.get('average_heartrate', 'N/A')
    prompt = f"Human: You are a friendly and encouraging running coach. Analyze the following workout data and provide 2-3 personalized, insightful, and motivational comments. Keep the tone positive. Format the output as a short summary paragraph followed by a markdown bulleted list.\n\nHere is the workout data:\n- Type: {activity_data.get('type', 'Workout')}\n- Distance: {distance_km:.2f} km\n- Moving Time: {moving_time_min:.1f} minutes\n- Average Pace: {avg_pace_min_km:.2f} min/km\n- Average Heart Rate: {avg_hr} bpm\n\nAssistant:"

    # --- Invoke Amazon Bedrock ---
    try:
        bedrock = boto3.client('bedrock-runtime')
        response = bedrock.invoke_model(
            body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 500, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}),
            modelId='anthropic.claude-3-sonnet-20240229-v1:0', contentType='application/json', accept='application/json'
        )
        ai_insights = json.loads(response.get('body').read())['content'][0]['text']
    except Exception as e:
        print(f"ERROR: Bedrock invocation failed: {e}")
        return {'statusCode': 500, 'body': json.dumps('Failed to get insights from AI model.')}
    
    # --- Save workout to DynamoDB ---
    try:
        workouts_table = boto3.resource('dynamodb').Table(workouts_table_name)
        workouts_table.put_item(Item={
            'activity_id': activity_id, 'athlete_id': athlete_id, 'type': activity_data.get('type', 'Workout'),
            'distance_km': Decimal(str(round(distance_km, 2))), 'moving_time_min': Decimal(str(round(moving_time_min, 1))),
            'avg_pace_min_km': Decimal(str(round(avg_pace_min_km, 2))), 'avg_hr': str(avg_hr), 'ai_insights': ai_insights,
            'activity_date': activity_data.get('start_date_local', ''), 'event_time': int(time.time())
        })
    except Exception as e:
        print(f"ERROR: Failed to save workout to DynamoDB: {e}")

    # --- Update activity on Strava ---
    new_description = f"{activity_data.get('description', '')}\n\n🤖 AI-Powered Insights:\n{ai_insights}"
    try:
        res = requests.put(activity_url, headers=headers, json={'description': new_description})
        res.raise_for_status()
        print(f"Successfully updated activity {activity_id} with AI insights.")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to update activity {activity_id}. Response: {e.response.text if e.response else 'No response'}")
        return {'statusCode': 502, 'body': json.dumps('Could not update Strava activity.')}

    return {'statusCode': 200, 'body': json.dumps(f'Successfully processed activity {activity_id}')}
