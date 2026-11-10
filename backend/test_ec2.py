
import boto3

# Connect to LocalStack EC2
ec2 = boto3.client(
    "ec2",
    endpoint_url="http://localhost:4566",
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name="us-east-1"
)

# Create a simulated EC2 instance
response = ec2.run_instances(
    ImageId="ami-12345678",
    InstanceType="t2.micro",
    MinCount=1,
    MaxCount=1
)

instance_id = response["Instances"][0]["InstanceId"]

print("EC2 instance created successfully!")
print("Instance ID:", instance_id)

# Verify instance
instances = ec2.describe_instances()

for reservation in instances["Reservations"]:
    for instance in reservation["Instances"]:
        print(
            "Instance:",
            instance["InstanceId"],
            "| State:",
            instance["State"]["Name"]
        )
