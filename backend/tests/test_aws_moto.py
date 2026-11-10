import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def mocked_aws(aws_credentials):
    with mock_aws():
        yield


def test_s3_bucket_creation(mocked_aws):
    s3 = boto3.client("s3", region_name="us-east-1")

    s3.create_bucket(Bucket="guardrail-test-bucket")

    response = s3.list_buckets()
    bucket_names = [bucket["Name"] for bucket in response["Buckets"]]

    assert "guardrail-test-bucket" in bucket_names


def test_ec2_instance_creation(mocked_aws):
    ec2 = boto3.client("ec2", region_name="us-east-1")

    response = ec2.run_instances(
        ImageId="ami-12345678",
        InstanceType="t2.micro",
        MinCount=1,
        MaxCount=1
    )

    instance = response["Instances"][0]

    assert instance["InstanceId"].startswith("i-")
    assert instance["InstanceType"] == "t2.micro"
    assert instance["State"]["Name"] == "pending"
