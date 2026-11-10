
import boto3

ec2 = boto3.client(
    "ec2",
    endpoint_url="http://localhost:4566",
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test"
)

# Create an EC2 instance
response = ec2.run_instances(
    ImageId="ami-df5de72bdb3b",
    InstanceType="t3.nano",
    MinCount=1,
    MaxCount=1
)

instance_id = response["Instances"][0]["InstanceId"]

print("SUCCESS: EC2 instance created!")
print("Instance ID:", instance_id)

# Verify the instance
instances = ec2.describe_instances()

for reservation in instances["Reservations"]:
    for instance in reservation["Instances"]:
        print(
            "Instance:",
            instance["InstanceId"],
            "State:",
            instance["State"]["Name"]
        )
