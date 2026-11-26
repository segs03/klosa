"""Small pure-Python helpers for face templates and network ids. No Django, no numpy."""
import array
import ipaddress
import math


def mean_vector(vectors):
    vectors = [list(v) for v in vectors]
    if not vectors or not vectors[0]:
        raise ValueError("No vectors to average.")
    dims = len(vectors[0])
    if any(len(v) != dims for v in vectors):
        raise ValueError("Vectors have different lengths.")
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]


def normalize(vector):
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise ValueError("Cannot normalise a zero vector.")
    return [x / norm for x in vector]


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError("Vectors have different lengths.")
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def pack(vector):
    return array.array("f", vector).tobytes()


def unpack(data):
    out = array.array("f")
    out.frombytes(bytes(data))
    return list(out)


def network_id(ip):
    """The customer's network, not their exact address: an IPv4 /24 or an IPv6 /64. None if unreadable."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network((addr, prefix), strict=False))
