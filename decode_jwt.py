#!/usr/bin/env python3
import base64
import json
import sys

def decode_jwt_payload(token):
    # Split JWT into parts
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")

    # Get payload (second part)
    payload = parts[1]

    # Add padding if needed
    payload += '=' * (4 - len(payload) % 4)

    # Decode base64
    decoded = base64.b64decode(payload)

    # Parse JSON
    return json.loads(decoded)

if __name__ == "__main__":
    token = "eyJraWQiOiIrTmdmNVFCdDhCa2lYYVVNSEZEWk10bjkwbmRUaVlxWTM4K2lCZ3lvTDE4PSIsImFsZyI6IlJTMjU2In0.eyJzdWIiOiI5NDI4NzQ5OC1kMDExLTcwMWYtOTBkZi0zYjEyMGUzZGE5ZTYiLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwiaXNzIjoiaHR0cHM6XC9cL2NvZ25pdG8taWRwLnVzLWVhc3QtMS5hbWF6b25hd3MuY29tXC91cy1lYXN0LTFfNEtKOHZaNFFWIiwiY29nbml0bzp1c2VybmFtZSI6Ijk0Mjg3NDk4LWQwMTEtNzAxZi05MGRmLTNiMTIwZTNkYTllNiIsIm9yaWdpbl9qdGkiOiI0ZGNkOTU3NC0yZmZlLTRjNTItYWUyMy0xZDEwY2U1NGE1MTYiLCJhdWQiOiI3N3FqY3MxdmtlZ3A2YnF0dDFndjFkaHJoMCIsImV2ZW50X2lkIjoiZTNhYzFlZTMtYjkzMS00MGE0LWE0NjItMjFjZmIwMGMwOTkzIiwidG9rZW5fdXNlIjoiaWQiLCJhdXRoX3RpbWUiOjE3NzIzODA1MzgsImV4cCI6MTc3MjM4NDEzOCwiaWF0IjoxNzcyMzgwNTM5LCJqdGkiOiI2YjE0MGRhMS01NGI3LTQwMDYtYmZkNi0zNTY5ODJjODZmYTgiLCJlbWFpbCI6InRlc3R1c2VyQGV4YW1wbGUuY29tIn0.D3da65BJ2gIcD0axy6xREO6JlY7RimPzQ5rRoPZYbm2PKEQC6WfGazbZpiSEohpGVzI6QegCoejBRd1cjl7CsmsTd1417AhEXNmTzDI23jUu-Y_xm7lpTkXWt1D_iNf5SMD8aT3N0vz6vc49cZhnuie8BTNFigZl3n0PdCwPe-wWshnMDjSWauOBm4p9pIj5PAp5LyTOmcZ642YILvXtg1Ids9AOInjeRhnSFg3Fc6c-jIK6b8lkLsKrbDnMoyvSf5MeloQYSD8MdEt7kbUPbOfT9bGcUrFTBlZmmfssvqow7USZT6epCiWZmDUHU0S9jagRnunGec5-8sKVKo4BTA"

    payload = decode_jwt_payload(token)
    print(json.dumps(payload, indent=2))