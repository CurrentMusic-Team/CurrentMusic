"""Parse client addresses and identify Cloudflare edge addresses."""

import ipaddress

# Keep in sync with deploy/nginx-cloudflare-realip.conf.example.
CLOUDFLARE_NETWORKS = tuple(map(ipaddress.ip_network, (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22", "2400:cb00::/32",
    "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)))


def parse_ip(value):
    """Parse a bare IPv4 or IPv6 address and normalize IPv4-mapped IPv6."""
    value = str(value or "").strip()
    if "%" in value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        return address.ipv4_mapped or address
    return address


def region_address(value):
    """Return a public address eligible for an IP region lookup."""
    address = parse_ip(value)
    if (address is None or not address.is_global or address.is_multicast
            or address.is_reserved or any(address in net for net in CLOUDFLARE_NETWORKS)):
        return None
    return address
