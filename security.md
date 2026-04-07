# 🎯 Goal
You want a system where:

1. Admin uploads a plugin/binary/algorithm file
2. Server stores it safely
3. Workers download it
4. Workers verify it is authentic (not modified)
5. Workers execute it safely
6. Workers return results

---

# 🔥 The Real Threat Model (what we must protect against)

### Attack possibilities:

✅ Someone uploads a fake plugin pretending to be you
✅ Someone modifies plugin while worker downloads it
✅ Worker downloads correct plugin but attacker swaps file locally
✅ Worker executes malicious binary
✅ Worker pretends plugin succeeded and sends fake results
✅ Worker tries to run unauthorized plugin not assigned

So we need:

* **authenticity** (plugin came from server/admin)
* **integrity** (plugin not modified)
* **authorization** (worker allowed to run it)
* **replay protection** (old plugin/task cannot be reused)
* **safe execution** (sandboxing)

---

# 🧠 The Correct System: Digital Signatures

There are 2 crypto tools people confuse:

## 1) Hash (SHA256)

Detects file modification.

But **hash is not enough** because attacker can replace both plugin and hash.

## 2) Digital Signature (Ed25519 / RSA)

Proves the file was signed by the owner.

This is what you need.

---

# ✅ Best Setup: Ed25519 Signing

Use **Ed25519** because:

* fast
* small keys
* easy to implement
* modern + secure

---

# 🔑 Key Ownership Model

## Admin Signing Key (PRIVATE KEY)

Stored only on server/admin machine.

Used to sign plugins.

## Public Key

Embedded into every worker executable permanently.

Used to verify plugins.

So even if the server is attacked, workers only trust plugins signed by your private key.

---

# 📦 Plugin Upload Pipeline (Server Side)

When admin uploads a plugin:

### Step 1: Compute hash

Server computes:

```
sha256(plugin_file)
```

This gives you a fingerprint like:

`4a8d...c91f`

### Step 2: Create Manifest (metadata file)

Make a JSON manifest like:

```json
{
  "plugin_id": "matrix_mul_v3",
  "version": "3.0",
  "sha256": "4a8d...c91f",
  "entrypoint": "run.exe",
  "created_at": 1712500000,
  "permissions": ["cpu", "ram"],
  "platform": "windows-x64"
}
```

This manifest is extremely important.

### Step 3: Sign the Manifest

Now you sign the manifest with private key:

```
signature = Sign(private_key, sha256(manifest_bytes))
```

Now you store:

* plugin binary file
* manifest.json
* manifest.sig

---

# 📂 How to Store It

Your server should store like:

```
plugins/
  matrix_mul_v3/
    plugin.exe
    manifest.json
    manifest.sig
```

Or better: store plugins by hash (immutable storage)

```
plugins/
  4a8d...c91f/
    plugin.exe
    manifest.json
    manifest.sig
```

This prevents accidental overwrites.

---

# 📡 Worker Download Pipeline

When worker asks for a task, server replies:

```json
{
  "task_id": "task_993",
  "plugin_id": "matrix_mul_v3",
  "plugin_url": "/download/4a8d...c91f/plugin.exe",
  "manifest_url": "/download/4a8d...c91f/manifest.json",
  "sig_url": "/download/4a8d...c91f/manifest.sig",
  "input_data_url": "/task/task_993/input.bin"
}
```

---

# 🛡️ Worker Verification Pipeline (MOST IMPORTANT)

Worker downloads:

* plugin.exe
* manifest.json
* manifest.sig

Now worker does:

---

## Step 1: Verify manifest signature

Worker computes:

```
hash_manifest = sha256(manifest.json)
```

Then verifies:

```
Verify(public_key, hash_manifest, manifest.sig)
```

If verification fails → plugin is rejected.

⚠️ This is the key step.
It guarantees: **manifest was signed by you.**

---

## Step 2: Verify plugin integrity

Worker computes:

```
sha256(plugin.exe)
```

and compares it with manifest’s sha256.

If mismatch → reject plugin.

Now you guarantee: **plugin is exactly what admin uploaded.**

---

## Step 3: Verify compatibility

Manifest says:

```json
"platform": "windows-x64"
```

Worker checks if it matches.

---

# ✅ At this point worker trusts the plugin.

Now it can execute.

---

# 🔥 Why Sign Manifest Instead of Plugin Directly?

Because manifest can include:

* hash
* version
* entrypoint
* permissions
* required RAM
* required CPU instruction set
* arguments format

You’re signing the *whole contract*, not just the file.

---

# 🧩 Execution Model (How plugin gets input without stdin)

You mentioned stdin is bad and config files are dumb.

Correct.

The clean solution is:

### Use a single binary protocol input passed via file descriptor or temp file.

---

# Best Practical Method: Input File in Temp Folder

Worker creates a job directory:

```
jobs/task_993/
  plugin.exe
  input.bin
  output.bin
  logs.txt
```

Plugin is executed like:

```
plugin.exe input.bin output.bin
```

This is clean, fast, and universal.

No messy JSONs.

---

# 🔒 But isn't input.bin a file? Won’t it create many files?

Yes but that’s normal in real systems.

Even Docker + Kubernetes works like this.

And you can auto-delete job folders after completion.

---

# ⚡ Even Better Method: Pipe Input via Memory (Advanced)

Worker runs plugin and streams input through pipe:

* worker spawns plugin process
* writes binary input into plugin STDIN
* plugin outputs result into STDOUT

But you said stdin won’t do.

Actually stdin is *very good* if you use it as a binary stream, but debugging becomes harder.

---

# 🧠 Best compromise:

Use **input.bin/output.bin file method**.

---

# 🛡️ Prevent Plugin Theft / Reuse

A worker could steal plugin.exe and reuse it.

If that matters, you add **task-bound signing**.

---

# 🔥 Task-Bound Token Signing (Prevents Replay)

When server assigns task, it sends worker a token:

```json
{
  "task_id": "task_993",
  "plugin_hash": "4a8d...c91f",
  "expires_at": 1712500500,
  "nonce": "random_128bit"
}
```

Server signs it:

```
task_sig = Sign(private_key, sha256(task_token))
```

Worker verifies this too.

Then worker passes token into plugin execution:

```
plugin.exe input.bin output.bin token.bin
```

Now plugin can verify token too (if plugin contains public key).

This ensures plugin runs only on valid assigned tasks.

---

# 🧱 Plugin Format Options

You have 3 choices:

---

## Option A: Raw Binary (.exe)

Worker downloads and executes.

✔ simplest
❌ most dangerous if sandboxing weak

---

## Option B: Plugin as a Zip Bundle

Upload a zip containing:

```
plugin_bundle.zip
  plugin.exe
  manifest.json
  manifest.sig
  README.txt
```

Worker extracts and runs.

✔ clean
✔ scalable
✔ supports dependencies

---

## Option C: WebAssembly (WASM)

Instead of running .exe, run WASM inside sandbox runtime.

✔ very secure
✔ cross platform
✔ safe execution
❌ harder to implement initially

If you want *real security*, WASM is the best future direction.

---

# 🛡️ Sandbox Execution (Critical)

Even if plugin is signed, it can still be harmful if you signed a bad one accidentally.

So you still sandbox workers.

---

## Sandbox Level 1 (Basic)

Run plugin with restricted permissions:

* run as low privilege user
* no admin rights
* no filesystem access outside job folder

On Linux: namespaces + seccomp
On Windows: restricted token + Job Objects

---

## Sandbox Level 2 (Docker Container)

Worker runs plugin inside container.

✔ strong isolation
✔ easy resource limits

---

## Sandbox Level 3 (WASM Runtime)

Best.

Plugin cannot access system unless you allow.

---

# 🧠 Recommended Execution Strategy for You

### For now (fast implementation):

* zip bundle plugin
* manifest signing
* verify signature
* run plugin in job folder
* enforce timeout + memory limits
* delete after completion

Later:

* migrate plugins to WASM

---

# 📌 Secure Transport Layer

Even though signing protects integrity, you still should use:

### HTTPS/TLS

* prevents sniffing
* prevents MITM attacks
* protects session tokens

Signing protects file integrity, TLS protects privacy + authentication.

Use both.

---

# 🔥 Full Plugin Lifecycle (End-to-End)

## 1. Admin builds plugin

Produces `plugin.exe`

## 2. Server creates manifest

Computes sha256

## 3. Server signs manifest

Creates signature

## 4. Worker requests task

Server responds with plugin hash + URLs

## 5. Worker downloads plugin bundle

## 6. Worker verifies:

* manifest signature
* plugin hash

## 7. Worker runs plugin with:

* input.bin output.bin

## 8. Worker uploads output + logs

## 9. Server verifies result integrity (optional)

You can include:

* output hash
* execution log
* runtime stats

---

# 🧾 Example Manifest (Final Version)

```json
{
  "plugin_id": "matrix_mul",
  "version": 3,
  "build": "2026-04-07",
  "platform": "windows-x64",
  "entrypoint": "plugin.exe",
  "sha256": "4a8d...c91f",
  "min_ram_mb": 256,
  "timeout_sec": 30,
  "requires": ["cpu"],
  "args": ["input.bin", "output.bin"]
}
```

Then sign it.

---

# 🧠 Important: Where to Store Public Key?

Inside worker code.

Hardcoded like:

```rust
const ADMIN_PUBLIC_KEY: [u8; 32] = [...];
```

So attacker cannot change it unless they recompile worker.

---

# 🔥 Key Rotation (Advanced but important)

If your signing key leaks, game over.

So you need a rotation strategy.

### Method:

* support multiple public keys in worker
* manifest contains key_id
* worker checks signature using correct public key

Manifest:

```json
{
  "key_id": "admin_key_2026",
  ...
}
```

Worker:

```rust
match key_id {
  "admin_key_2026" => verify(pk1),
  "admin_key_2027" => verify(pk2),
}
```

---

# 🚨 How Workers Should Reject Plugins

Worker should reject if:

* manifest signature invalid
* plugin hash mismatch
* plugin version outdated (optional)
* plugin requires too much RAM
* plugin not assigned by server
* plugin not in allowlist

---

# 🏆 Bonus: Prevent Worker Spoofing Results

Even if plugin is secure, a worker can fake output.

Solution:

### Redundant computation (classic distributed systems trick)

Assign same task to 2 workers randomly.

If results match → trust.

If mismatch → mark one as suspicious.

This is how BOINC-style systems do it.

---

# ✅ Summary Architecture (What You Should Implement)

### Use this exact secure system:

* Plugin stored with SHA256 hash
* Manifest JSON created
* Manifest signed using Ed25519 private key
* Worker has public key hardcoded
* Worker downloads plugin + manifest + signature
* Worker verifies signature then verifies hash
* Only then executes plugin
* Runs plugin inside job folder with timeout + restricted permissions

This gives you:
✅ authenticity
✅ integrity
✅ anti-tampering
✅ anti-MITM
✅ controlled execution

---

