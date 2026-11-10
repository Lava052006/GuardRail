from decimal import Decimal


class CostEstimator:

    # Illustrative USD estimates.
    # These values can be changed through configuration later.

    EC2_HOURLY_RATES = {
        "t2.micro": Decimal("0.0116"),
        "t2.small": Decimal("0.0230"),
        "t3.micro": Decimal("0.0104"),
        "t3.small": Decimal("0.0208"),
    }

    S3_RATES = {
        "PUT": Decimal("0.005"),
        "GET": Decimal("0.0004"),
        "DELETE": Decimal("0.0000"),
    }

    S3_STORAGE_PER_GB_MONTH = Decimal("0.023")

    @classmethod
    def estimate_ec2(cls, instance_type: str, hours: Decimal) -> Decimal:
        rate = cls.EC2_HOURLY_RATES.get(instance_type)

        if rate is None:
            raise ValueError(f"Unsupported EC2 instance type: {instance_type}")

        if hours <= 0:
            raise ValueError("Hours must be greater than zero")

        return (rate * hours).quantize(Decimal("0.000001"))

    @classmethod
    def estimate_s3_request(cls, operation: str, requests: int) -> Decimal:
        operation = operation.upper()

        if operation not in cls.S3_RATES:
            raise ValueError(f"Unsupported S3 operation: {operation}")

        if requests < 0:
            raise ValueError("Request count cannot be negative")

        rate_per_thousand = cls.S3_RATES[operation]

        return (
            rate_per_thousand * Decimal(requests) / Decimal("1000")
        ).quantize(Decimal("0.000001"))

    @classmethod
    def estimate_s3_storage(cls, size_gb: Decimal, months: Decimal) -> Decimal:
        if size_gb < 0 or months < 0:
            raise ValueError("Storage size and duration cannot be negative")

        return (
            cls.S3_STORAGE_PER_GB_MONTH * size_gb * months
        ).quantize(Decimal("0.000001"))
