Strava AI Insights - Hackathon Setup Guide
------------------------------------------

This guide outlines the steps to deploy the serverless application on AWS.

### Architecture Overview

As you designed, the flow is:

1.  **SPA (S3/CloudFront)**: User clicks "Connect with Strava".
    
2.  **Strava OAuth**: User authorizes the app and is redirected to our API Gateway.
    
3.  **API Gateway -> Lambda (`handle_strava_oauth`)**: The first Lambda exchanges the auth code for tokens and saves them to DynamoDB.
    
4.  **Strava Webhook**: A new activity in Strava sends an event to a separate API Gateway endpoint.
    
5.  **API Gateway -> Lambda (`process_strava_webhook`)**: EventBridge triggers the second Lambda with the activity details.
    
6.  **Lambda Logic**:
    
    *   Fetches user tokens from DynamoDB.
        
    *   Refreshes tokens if necessary.
        
    *   Fetches the full activity data from Strava.
        
    *   Calls **Amazon Bedrock** for analysis.
        
    *   Posts the analysis back to the Strava activity description.
        

### Simple Graphic

```
[SPA HTML] 
    |
    v
[S3 + CloudFront]
    |
    v
[Connect with Strava CTA] ---> [STRAVA Login Popup]
                                      |
                                      v
                            [STRAVA Webhook Trigger]
                                      |
                                      v
                               [API Gateway]
                                      |
         +----------------------------+----------------------------+
         |                                                         |
         v                                                         v
     [DynamoDB] <-- store user info                            [Lambda]
                                                                  |
                                    +-----------------------------+
                                    |
                              [Amazon Bedrock] <-- AI process
                                    |
                                    v
                              [POST AI back to Strava]
```


### AWS Setup Steps

#### 1\. Strava Application Setup

*   Created `https://www.strava.com/settings/api` a new application.
    
*   Noted my **Client ID** and **Client Secret**.
    
*   For the **Authorization Callback Domain**, I've used domain of my API Gateway (`earj4ucl80.execute-api.eu-central-1.amazonaws.com`). 
    

#### 2\. DynamoDB Table

*   Inside DynamoDB console I created two new tables.
    
*   **Table Name**: `strava-ai-users`.

*   **Table Name**: `strava-ai-workouts`.
    
*   **Primary Key**: `athlete_id` with type `Number`.
    
    

#### 3\. IAM Role for Lambdas

Created a single IAM Role that both Lambda functions use.

*   **Name**: `StravaAIHackathonLambdaRole`
    
*   **Permissions**: Attached the following AWS managed policies:
    
    *   `AWSLambdaBasicExecutionRole` (for CloudWatch Logs)
        
*   Create a new inline policy with the following JSON to grant necessary permissions:
    
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "VisualEditor0",
                    "Effect": "Allow",
                    "Action": [
                        "dynamodb:PutItem",
                        "bedrock:InvokeModel",
                        "dynamodb:GetItem",
                        "dynamodb:UpdateItem"
                    ],
                    "Resource": [
                        "arn:aws:bedrock:eu-central-1::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0",
                        "arn:aws:dynamodb:eu-central-1:12345678ID:table/strava-ai-users",
                        "arn:aws:dynamodb:eu-central-1:12345678ID:table/strava-ai-workouts"
                    ]
                },
                {
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": "arn:aws:lambda:eu-central-1:12345678ID:function:handle_strava_oauth"
                },
                {
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": "arn:aws:lambda:eu-central-1:12345678ID:function:process_strava_webhook"
                }
            ]
        }
        
    

#### 4\. Lambda Dependencies Layer

Your Python code requires the `requests` library, which is not included in the standard Lambda runtime. I created a Lambda Layer in form of zip file and added it as layer - to provide this dependency.

1.  **In CloudShell** I executed next commands to add custom layer
    
        # created new env
        python -m venv venv
        # activated and switch to it
        source venv/bin/activate
        # created new directory
        mkdir python
        # added required lib
        pip install -t python requests
        # zipped it into archive
        zip -r9 requests.zip python
        # created custom layer and added created zip 
        aws lambda publish-layer-version --layer-name required_requests_lib --compatible-runtime python3.12 --compatible-architectures x86_64 --zip-file fileb://requests.zip --no-cli-pager
        
    
2.  In the AWS Lambda console, go to **Layers** and in UI added newly created `required_requests_lib` layer.
    

#### 5\. Lambda Functions

Created two Lambda functions using the Python 3.12 runtime and the IAM role created above. For each function, after creating it I also added my custom layer.


**Function 1: `handle_strava_oauth`**

*   **Handler**: `handle_strava_oauth.handle_strava_oauth`
    
*   **Environment Variables**:
    
    *   `STRAVA_CLIENT_ID`: (Strava Client ID)
        
    *   `STRAVA_CLIENT_SECRET`: (Strava Client Secret)
        
    *   `DYNAMODB_TABLE_NAME`: (DynamoDB table for user auth)

**Function 2: `process_strava_webhook`**

*   **Handler**: `process_strava_webhook.process_strava_webhook`
    
*   **Environment Variables**:
    
    *   `STRAVA_CLIENT_ID`: (Strava Client ID)
        
    *   `STRAVA_CLIENT_SECRET`: (Strava Client Secret)
        
    *   `DYNAMODB_TABLE_NAME`: (DynamoDB table for user auth)
        
*   **Increase Timeout**: Increased it to 30 seconds to be safe!
    

#### 6\. API Gateway

Created two new **REST API** Endpoints.

1.  **OAuth Callback Endpoint**:
    
    *   Create a resource: `/strava/callback`.
        
    *   Create a `GET` method on this resource.
        
    *   Integration type: **Lambda Function**.
        
    *   Select your `handle_strava_oauth` Lambda function.
        
2.  **Webhook Ingestion Endpoint**:
    
    *   Create a resource: `/strava/webhook`.
        
    *   Create a `POST` method on this resource.
        
    *   Integration type: **Lambda Function**.
        
    *   Select your `process_strava_webhook` Lambda function.
        

### Testing connection with custom payload

-   Example payload (need to test with real object_id - found in url and your strava ID) to catch and test in CloudWatch


        {
            "aspect_type": "create",
            "event_time": 1516126040,
            "object_id": 1360128428,
            "object_type": "activity",
            "owner_id": 134815,
            "subscription_id": 120475,
            "updates": {
                "title": "Messy"
            }
        }

For all STRAVA developer related stuff I contacted API dev page from here: https://developers.strava.com/docs/webhooks/#:~:text=an%20activity's%20privacy.-,Subscriptions,com/api/v3/push_subscriptions

