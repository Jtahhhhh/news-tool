"""Generate a password hash without putting the password in shell history."""
import getpass
import hashlib
import secrets

password = getpass.getpass('Dashboard password (at least 12 characters): ')
if len(password) < 12:
    raise SystemExit('Use at least 12 characters')
salt = secrets.token_bytes(16)
digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
print('scrypt$' + salt.hex() + '$' + digest.hex())
