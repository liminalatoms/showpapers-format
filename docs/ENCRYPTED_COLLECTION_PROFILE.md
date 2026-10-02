# Protected Files

The optional protected envelope encrypts an unchanged readable collection. It is selected-content exchange, not a full-library recovery format. A separate recovery key is required to open it.

## Wire Contract

- Encrypted file prefix: exact ASCII `SHOWPAPERS-SEALED\n1\nTINK-AES128-GCM-HKDF-1MB\n\n`, followed by Tink Streaming AEAD ciphertext. The entire exact prefix is the associated data. No private metadata appears in the prefix.
- Key file prefix: exact ASCII `SHOWPAPERS-KEY\n1\nTINK-AES128-GCM-HKDF-1MB\n\n`, followed by a canonical binary Tink keyset. Maximum 4096 bytes including prefix. This is secret key material, never included with the protected collection or stored in analytics/logs/SavedState. The UI must explain that the key opens the copy and should be kept separately.
- Exactly one ENABLED RAW symmetric `type.googleapis.com/google.crypto.tink.AesGcmHkdfStreamingKey`, primary ID equals its nonzero protobuf uint32 key ID (1–4294967295). Key version 0; 16-byte random key; derived key size 16; HKDF SHA256; ciphertext segment size 1048576. Reject any other key/profile and noncanonical/unknown/duplicate protobuf fields by rebuilding the admitted known fields and comparing canonical bytes before use.
- Tink generates the key and fresh stream randomness. The envelope does not define a password-based key derivation scheme.
- Plaintext remains bounded by the existing 103 MiB archive cap. Encrypted file cap is 104 MiB. Decryption must reach authenticated EOF and validate the entire inner archive before returning any inventory or permitting imports. Truncation, trailing bytes, wrong keys, altered segments or headers fail closed.
- Stage decrypted data in private temporary storage. Never expose partial plaintext in a preview or import. Collection identity and import checks use the authenticated inner archive digest.
- Encrypted source Save Copy stays encrypted with the same key and fresh stream randomness. No silent readable downgrade. Readable sources keep their current behavior. Original sources and native vault records remain untouched by editing/export.
- Implementations must keep secret key material out of logs, analytics and saved UI state. Retire key access when the authorized workflow ends. Report cancelled or incomplete saves accurately.


## Reader And Writer Requirements

Use Tink’s AES128_GCM_HKDF_1MB template and validate both the envelope and its complete plaintext. Test cross-implementation opening, wrong keys, malformed prefixes and keysets, bounds, multi-segment corruption, truncation and trailing bytes. No failed open may expose partial contents.

Keep and share the recovery key separately from the encrypted collection. Anyone with both can open the copy. Losing the key makes the protected copy unavailable; the format defines no key recovery service.

The [Tink Streaming AEAD documentation](https://developers.google.com/tink/encrypt-large-files-or-data-streams) describes the cryptographic primitive. Application support for protected files is separate from readable collection support; consult [App Support](https://protocol.showpapers.app/changelog#app-support).
