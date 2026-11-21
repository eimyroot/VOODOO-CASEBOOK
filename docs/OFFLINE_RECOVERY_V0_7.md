# VOODOO-CASEBOOK v0.7 — offline encrypted transfer and agent boundary

**State:** isolated review candidate. No production release, key-management implementation, second-machine recovery, network API, background daemon, CASER or agent grant.

## Source and chosen design

The canonical project README, code and tests are the actual state. VOODOO OS Evolution is a supporting noncanonical source. Its compartmentalized trust-domain pattern maps here to operator-only encrypted transfer plus a separate future credential/agent broker.

The VoodooBook provides LibreSSL 3.3.6 via /usr/bin/openssl. age, GPG and Python cryptography were absent at inspection. This candidate uses CMS EnvelopedData with AES-256-CBC and recipient X.509 certificate; this is **encryption, NOT authenticated encryption**. Do not claim integrity or authenticity from the CMS envelope alone.

A preexisting fully verified backup is the only export source. The tool does not copy a live WAL SQLite database. The private key never enters the export process. The certificate **must be validated independently and its exact SHA-256 PIN supplied BEFORE encryption**. The operator must obtain that pin through a separate trusted channel; a pin printed by a substituted certificate is not authorization. The implementation then uses a temporary owner-only copy of the pinned certificate bytes during CMS operations to reduce post-check substitution risk. Both ciphertext and plaintext SHA-256 plus CASEBOOK head hash must be retained separately in a trusted channel. Metadata shipped with the ciphertext is not independently trusted.

## Operator workflow (after source acceptance only)

1. Verify existing CASEBOOK local snapshot and backup ID.
2. Authenticate the public recipient certificate and independently record its SHA-256 **before export**. Keep the private key under control of the separate recovery operator/host.
3. Export only the named verified snapshot to a brand-new owner-only encrypted CMS package outside 0_DEV and the active data root.
4. Independently preserve the reported envelope_sha256, database_sha256 and head_hash. The values must NOT be trusted solely from an accompanying manifest on the same media.
5. Transfer the encrypted CMS to independent off-host storage and verify its bytes there.
6. On the recovery host, invoke decryption with the securely stored private key and the independently pinned values. The destination parent must exist and be owner-only; the destination itself must not yet exist.
7. Decrypt to a new **private staging directory**, not to the final restore path. Verify plaintext SHA-256 and the complete SQLite journal, fsync the validated result, and only then rename the staging directory to the requested target. Never switch a running service to the recovery path automatically.
8. A forced process kill (SIGKILL or power loss) may leave private **plaintext** in a directory named `.casebook-restore-incomplete-*` beneath the owner-only destination parent. It must not be mistaken for a valid restore. Securely quarantine and inspect any such leftover before separately authorized cleanup; do not delete unknown user directories or claim secure erase on SSDs.
9. Last-moment target recheck reduces accidental conflicts, but directory rename is not a general protection against a malicious process with the same UID racing filesystem operations. See [agent isolation gate](AGENT_ISOLATION_GATE.md).

From an APPROVED checkout, a human operator can invoke:

    python3 -B -m voodoo_casebook.offline_transfer export --data-root /ABS/CASEBOOK-DATA --backup-id VERIFIED_ID --recipient-cert /ABS/RECIPIENT-PUBLIC.pem --expected-recipient-cert-sha256 64_HEX_PRE_APPROVED_CERT_PIN --to /ABS/PRIVATE-TRANSFER/CASEBOOK.cms

A recovery operator uses an independent machine and an operator-held private key:

    python3 -B -m voodoo_casebook.offline_transfer restore --package /ABS/PRIVATE-TRANSFER/CASEBOOK.cms --recipient-cert /ABS/RECIPIENT-PUBLIC.pem --expected-recipient-cert-sha256 64_HEX_PRE_APPROVED_CERT_PIN --private-key /ABS/RECOVERY-KEY.pem --to /ABS/PRIVATE-RECOVERY/RESTORED-NEW --expected-package-sha256 64_HEX_PIN --expected-database-sha256 64_HEX_PIN --expected-head-hash 64_HEX_PIN

The current test interface requires a readable owner-only private key file. It does not implement password-encrypted private-key entry, hardware tokens, key rotation, certificate issuance or secure key custody. Never put a production key in ChatGPT, GitHub, the source repository or agent-writable paths. A legitimate off-host recovery receipt requires a separate verified device and independently available key, which is NOT done here.

## Agent security and least privilege

| Actor | Current state | Production gate |
| --- | --- | --- |
| Operator | Explicit local commands, owner-only files | Strong authorization, reversible actions, audit |
| CASEBOOK | Local SQLite, no network listener | No execution authority |
| CASER and agents | No new grant | Deny by default; scoped broker and verified provenance |
| Same-UID macOS processes | Can read owner-only 0600 data | Separate OS user/sandbox or authenticated broker |
| Off-host key custodian | Not configured | Separate key device/custodian and independent restore |

Do not grant agents the live database path, private decryption key, full disk access or a shell with the same UID and uncontrolled file access. 0700/0600 file modes are NOT isolation against same-UID agents.

## Evidence and blocked claims

Synthetic test fixtures exercise CMS export/decrypt, exact event identity and independently pinned certificate/digest/head values, wrong key, modified ciphertext, a valid-but-substituted certificate, source certificate replacement after preflight, bad certificate, unsafe permissions and overwrites. Additional subprocess tests force exit after successful decrypt but before verification: the **final target remains absent**, while only private incomplete staging may remain. Synthetic same-UID destination collision is denied without overwriting the other operator's directory. The source does not create a production key or export actual data.

Still BLOCKED: actual off-host copy and decrypt, independent key custody, private-key passphrase/hardware interface, signed external anchor, AEAD envelope, same-UID isolation, production recovery metrics, formal security approval and deployment authorization.

Next security review should evaluate authenticated modern envelope tooling (e.g. reviewed age/AEAD) or signed CMS + encryption before handling sensitive production data.
