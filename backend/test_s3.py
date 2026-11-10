
import boto3

# Connect to LocalStack
s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:4566",
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name="us-east-1"
)

# Create an S3 bucket
bucket_name = "guardrail-test-bucket"

s3.create_bucket(Bucket=bucket_name)

print("S3 bucket created successfully!")

# List all buckets
response = s3.list_buckets()

print("\nAvailable S3 buckets:")

for bucket in response["Buckets"]:
    print(bucket["Name"])
