"""
core/signing_key.py

The secret SIGNING KEY, built into the app.

Every submission is signed with this key (see core/submission.py) and the Tutor
tab checks the signature with the same key. Because every copy of the app holds
the same key, the student's app and the lecturer's app always agree, and nobody
has to set anything up.

The key is never written into a submission file or an exported assessment, so
a student does not see it in anything they are sent.

Honest limit: a key inside a program can be found by a determined person who
digs through the code. It stops casual editing of a submission, not a skilled
attacker. The planned fix is for the STM32 box to sign the data itself.

Changing this value makes every submission signed with the old value unreadable,
so choose it before the real assessment and keep it the same afterwards.
When the app is packaged as an .exe, this file is the one to replace with a new
random key (python -c "import secrets; print(secrets.token_hex(32))").
"""

SIGNING_KEY = b"8551a6b6f35858ea0d92043bb795bef2152e59879c00b9cc46e82404a44a3f8a"
