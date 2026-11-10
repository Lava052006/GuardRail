
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = "http://localhost:4566"

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test",
    config=Config(s3={"addressing_style": "path"})
)

bucket_name = "guardrail-test-bucket-001"

try:
    s3.create_bucket(Bucket=bucket_name)
    print("SUCCESS: S3 bucket created!")
except ClientError as e:
    if e.response["Error"]["Code"] in (
        "BucketAlreadyOwnedByYou",
        "BucketAlreadyExists"
    ):
        print("S3 bucket already exists.")
    else:
        raise

print("\nAvailable S3 buckets:")

for bucket in s3.list_buckets()["Buckets"]:
    print(bucket["Name"])
