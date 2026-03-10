# do not use as is but use the same logic or better, make it easy for the backend to use from an endpoint or a function call, this is just a template to get the logic right and test it out, you can then make it more robust and add more providers and patterns as needed
import sys
import dns.resolver

PROVIDER_PATTERNS = {
    "Google Workspace": ["google.com", "googlemail.com"],
    "Office 365 / Microsoft": ["mail.protection.outlook.com", "outlook.com"],
    "ProtonMail": ["protonmail.ch", "proton.me"],
    "Zoho Mail": ["zoho.com", "zohomail.com"],
    "Yahoo Mail": ["yahoo.com", "yahoodns.net"],
    "Mimecast (gateway)": ["mimecast.com"],
    "Proofpoint (gateway)": ["pphosted.com", "proofpoint.com"],
    "Barracuda (gateway)": ["barracudanetworks.com"],
    "Fastmail": ["fastmail.com", "fastmailbox.net"],
    "Mailchimp / Mandrill": ["mandrill.com"],
    "SendGrid": ["sendgrid.net"],
    "Amazon SES": ["amazonses.com"],
    "iCloud / Apple": ["icloud.com", "apple.com"],
    "Rackspace": ["emailsrvr.com"],
    "GoDaddy / Secureserver": ["secureserver.net"],
    "OVH": ["ovh.net", "ovhcloud.com"],
    "Namecheap / PrivateEmail": ["privateemail.com"],
}


def get_mx_records(domain: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return sorted(answers, key=lambda r: r.preference)
    except dns.resolver.NXDOMAIN:
        raise ValueError(f"Domain '{domain}' does not exist.")
    except dns.resolver.NoAnswer:
        raise ValueError(f"No MX records found for '{domain}'.")
    except dns.exception.DNSException as e:
        raise ValueError(f"DNS lookup failed: {e}")


def detect_provider(mx_records: list) -> str:
    for record in mx_records:
        hostname = str(record.exchange).rstrip(".").lower()
        for provider, patterns in PROVIDER_PATTERNS.items():
            if any(pattern in hostname for pattern in patterns):
                return provider
    return "Unknown provider"


def check_email(email: str) -> dict:
    if "@" not in email:
        raise ValueError("Invalid email address — missing '@'.")

    domain = email.split("@")[-1].strip().lower()
    mx_records = get_mx_records(domain)

    provider = detect_provider(mx_records)
    mx_hostnames = [str(r.exchange).rstrip(".") for r in mx_records]

    return {
        "email": email,
        "domain": domain,
        "provider": provider,
        "mx_records": mx_hostnames,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python email_provider.py <email>")
        print("Example: python email_provider.py someone@company.com")
        sys.exit(1)

    email = sys.argv[1]

    try:
        result = check_email(email)
        print(f"\nEmail   : {result['email']}")
        print(f"Domain  : {result['domain']}")
        print(f"Provider: {result['provider']}")
        print(f"MX Records:")
        for mx in result["mx_records"]:
            print(f"  - {mx}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()