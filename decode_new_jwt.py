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
    # New token from the latest authentication
    token = "eyJraWQiOiIrTmdmNVFCdDhCa2lYYVVNSEZEWk10bjkwbmRUaVlxWTM4K2lCZ3lvTDE4PSIsImFsZyI6IlJTMjU2In0.eyJzdWIiOiI5NDI4NzQ5OC1kMDExLTcwMWYtOTBkZi0zYjEyMGUzZGE5ZTYiLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwiaXNzIjoiaHR0cHM6XC9cL2NvZ25pdG8taWRwLnVzLWVhc3QtMS5hbWF6b25hd3MuY29tXC91cy1lYXN0LTFfNEtKOHZaNFFWIiwiY29nbml0bzp1c2VybmFtZSI6Ijk0Mjg3NDk4LWQwMTEtNzAxZi05MGRmLTNiMTIwZTNkYTllNiIsIm9yaWdpbl9qdGkiOiI2Njg1ZmU5MS0zMGYyLTRiNzYtOWE5Yi03MDk5OTE2ZGQ0YTMiLCJhdWQiOiI3N3FqY3MxdmtlZ3A2YnF0dDFndjFkaHJoMCIsImV2ZW50X2lkIjoiMDkyMjBhMWUtNjk5Ni00YWZkLTk2NWMtNGEzYWIxNWZhZDIyIiwidG9rZW5fdXNlIjoiaWQiLCJhdXRoX3RpbWUiOjE3NzIzODA4NDIsImV4cCI6MTc3MjM4NDQ0MiwiaWF0IjoxNzcyMzgwODQzLCJqdGkiOiIwODM4YmQxYS0yMDU2LTQ1YjUtYjBmYS05M2UxZjk2NmFmMGIiLCJlbWFpbCI6InRlc3R1c2VyQGV4YW1wbGUuY29tIn0.p8fDjHO9I2B2IEPiLIx8iJc0I5C5jkuu66awHK37D8_CJPtzYlSDTLSZfjXCVK5JKwLe0nuPPtH47ry_Ezs6P4A3ae4jyMHU7fnVpzi3zOLMDrDpsGENLbN9mQnLGT5dF_U4Y1AnWhWmrgAhmis8q-Sb-F2RV46pHPiKGMCakvc6NObOYyfuUQhdLqqPqKNpcUvMWqPkBo5FxSFZMEfmeVjNvHQ5hgkqKDCLNSNtW40nCWP3JfgWROYUj3R0usTEmy5h7e-H1v90l4srp15h0tVZbv-n1u_B9erFTl4V59fk-eHGsbzJ8NQAtriy_1CUkX88cCCSeU8dW-8jyXHpdw"

    payload = decode_jwt_payload(token)
    print(json.dumps(payload, indent=2))