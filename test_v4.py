import base64
import io
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import encryption_code_v4 as core
import enclave_crypto as crypto
import enclave_media as media
import enclave_workflow as workflow
import service_backend as backend


class V4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "v4 disposable passphrase for tests"
        crypto.SERVICE_CONTEXT = True
        cls.raw, _ = crypto.seal("V4 test fixture", cls.password, crypto.SINGLE)
        crypto.SERVICE_CONTEXT = False

    def setUp(self):
        try:
            self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        except PermissionError:
            self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = backend.database(Path(self.folder.name))
        self.addCleanup(self.db.close)
        self.owner = "S-1-5-21-1-2-3-1001"
        self.request("enroll", credential="test", public_key="public-test-fixture")

    def request(self, op, payload=None, **extra):
        return backend.dispatch(self.db, {"op": op, "owner": self.owner, "payload": payload or {}, **extra})

    def decrypt_payload(self, raw=None):
        return {"envelope": base64.b64encode(raw or self.raw).decode(), "password": "incorrect"}

    def test_v4_kdf_aead_and_legacy_rejection(self):
        with patch.object(crypto, "SERVICE_CONTEXT", True):
            self.assertEqual(crypto.unseal(crypto.parse_envelope(self.raw), self.password)[0], "V4 test fixture")
            damaged = self.raw[:-1] + bytes([self.raw[-1] ^ 1])
            with self.assertRaises(crypto.AuthenticationError):
                crypto.unseal(crypto.parse_envelope(damaged), self.password)
        old = self.raw[:4] + b"\x03" + self.raw[5:]
        with self.assertRaises(ValueError):
            crypto.parse_carrier(old)
        with self.assertRaises(crypto.AuthenticationError):
            crypto.unseal(crypto.parse_envelope(self.raw), self.password)

    def test_settings_reject_bypass_and_arbitrary_paths(self):
        for config in ({"require_windows_hello": False}, {"app_retry_cooldown": False},
                       {"verification_data_dir": "C:/user-controlled"}, {"unknown": True}):
            with self.assertRaises(ValueError):
                self.request("save_settings", {"settings": config})
        result = self.request("save_settings", {"settings": {"default_device_bound": False}})
        self.assertTrue(result["saved"])
        self.assertFalse(self.request("status")["settings"]["default_device_bound"])

    def test_all_carrier_modes_and_mismatched_pairs(self):
        with patch.object(crypto, "SERVICE_CONTEXT", True):
            for mode in (crypto.SINGLE, crypto.TWIN, crypto.CAMOUFLAGE, crypto.SPLIT_CAMOUFLAGE):
                raw, parts = crypto.seal("Version four", self.password, mode)
                carriers = tuple(crypto.parse_carrier(part) for part in parts)
                self.assertEqual(crypto.join_parts(carriers).data, raw)
                self.assertEqual(crypto.unseal(crypto.join_parts(carriers), self.password)[0], "Version four")
                if len(parts) == 2:
                    with self.assertRaises(ValueError):
                        crypto.join_parts(carriers[:1])
                    changed = bytearray(parts[1])
                    changed[-1] ^= 1
                    with self.assertRaises(crypto.AuthenticationError):
                        crypto.unseal(crypto.join_parts((carriers[0], crypto.parse_carrier(bytes(changed)))), self.password)

    def test_cooldown_is_account_wide_and_persistent(self):
        with patch.object(crypto, "unseal", side_effect=crypto.AuthenticationError("bad")), patch.object(backend.time, "sleep"):
            for i in range(3):
                changed = bytearray(self.raw)
                changed[10] ^= i
                with self.assertRaises(crypto.AuthenticationError):
                    self.request("decrypt", self.decrypt_payload(bytes(changed)))
        other = backend.database(Path(self.folder.name))
        try:
            with self.assertRaisesRegex(ValueError, "Retry protection"):
                backend.dispatch(other, {"op": "decrypt", "owner": self.owner, "payload": self.decrypt_payload()})
        finally:
            other.close()

    def test_crashed_attempt_is_reserved(self):
        with patch.object(crypto, "unseal", side_effect=RuntimeError("simulated crash")), patch.object(backend.time, "sleep"):
            with self.assertRaises(RuntimeError):
                self.request("decrypt", self.decrypt_payload())
        self.assertEqual(self.db.execute("SELECT failures FROM attempts WHERE owner=?", (self.owner,)).fetchone(), (1,))

    def test_invalid_typing_never_releases_plaintext_and_counts_as_failure(self):
        intervals = [90, 110, 150, 130]
        profile = (1, 1200, 224, core.compute_cadence_sig(intervals, 120))
        samples = (
            {"intervals": intervals, "mean": 0},
            {"intervals": intervals, "mean": 100},
            {"intervals": intervals, "std": 0},
            {"intervals": [0, 150]},
            {"intervals": [-1, 150]},
            {"intervals": [float("inf"), 150]},
            {"intervals": [float("nan"), 150]},
            {"intervals": [True, 150]},
            {"intervals": ["90", 150]},
            {"intervals": [150]},
            {"intervals": None},
            None,
        )
        with patch.object(crypto, "unseal", return_value=("fixture", profile)), patch.object(backend.time, "sleep"):
            for sample in samples:
                with self.subTest(sample=sample):
                    with self.db:
                        self.db.execute("DELETE FROM attempts")
                    with self.assertRaisesRegex(crypto.AuthenticationError, "Unable to authenticate"):
                        self.request("decrypt", {**self.decrypt_payload(), "typing": sample})
                    self.assertEqual(self.db.execute("SELECT failures FROM attempts").fetchone(), (1,))
            for _ in range(2):
                with self.assertRaises(crypto.AuthenticationError):
                    self.request("decrypt", {**self.decrypt_payload(), "typing": samples[0]})
            with self.assertRaisesRegex(ValueError, "Retry protection"):
                self.request("decrypt", {**self.decrypt_payload(), "typing": {"intervals": intervals}})

    def test_existing_v4_typing_profile_accepts_valid_sample(self):
        intervals = [90, 110, 150, 130, 100, 140, 120, 120]
        profile = (2, 1200, 187, core.compute_cadence_sig(intervals, 120))
        with (patch.object(crypto, "SERVICE_CONTEXT", True),
              patch.object(crypto.windows, "wrap_device_secret", return_value=(bytes(16), bytes(256))) as wrap,
              patch.object(crypto.windows, "unwrap_device_secret", side_effect=lambda *_: wrap.call_args.args[0]),
              patch.object(backend.time, "sleep")):
            # Keep real AES-GCM and Argon2id; stand in for the TPM wrapping round trip.
            for legacy in (False, True):
                with self.subTest(legacy=legacy):
                    if legacy:
                        with patch.object(crypto, "_pad_body", side_effect=lambda p, raw: crypto.PROFILE.pack(*p) + raw):
                            raw, _ = crypto.seal("V4 typing fixture", self.password, crypto.SINGLE, bound=True, profile=profile)
                    else:
                        raw, _ = crypto.seal("V4 typing fixture", self.password, crypto.SINGLE, bound=True, profile=profile)
                    payload = {**self.decrypt_payload(raw), "password": self.password,
                               "typing": {"intervals": intervals, "mean": 120, "std": math.sqrt(350)}}
                    self.assertEqual(self.request("decrypt", payload)["text"], "V4 typing fixture")

    def test_success_resets_failures_and_owner_isolation(self):
        with patch.object(crypto, "unseal", side_effect=crypto.AuthenticationError("bad")), patch.object(backend.time, "sleep"):
            with self.assertRaises(crypto.AuthenticationError):
                self.request("decrypt", self.decrypt_payload())
        with patch.object(crypto, "unseal", return_value=("fixture", (0, 0, 0, b"\0" * 8))), patch.object(backend.time, "sleep"):
            self.assertEqual(self.request("decrypt", self.decrypt_payload())["text"], "fixture")
        self.assertIsNone(self.db.execute("SELECT failures FROM attempts WHERE owner=?", (self.owner,)).fetchone())
        with self.assertRaises(ValueError):
            backend.dispatch(self.db, {"op": "decrypt", "owner": "S-1-5-21-1-2-3-1002", "payload": self.decrypt_payload()})

    def test_no_silent_reenrollment_or_unwrap_endpoint(self):
        with self.assertRaises(ValueError):
            self.request("enroll", credential="substitute", public_key="substitute")
        for op in ("unwrap", "raw_key", "reset_attempts"):
            with self.assertRaises(ValueError):
                self.request(op)

    def test_unenrollment_preserves_account_data_and_can_reenroll(self):
        self.request("save_settings", {"settings": {"default_device_bound": False}})
        self.db.execute("UPDATE users SET api_key=? WHERE owner=?", (b"protected-fixture", self.owner))
        self.db.execute("INSERT INTO attempts VALUES (?,?,?)", (self.owner, 2, 0))
        self.db.execute("INSERT INTO covers VALUES (?,?,?)", (self.owner, b"digest", "fixture cover"))
        self.db.commit()
        self.assertFalse(self.request("unenroll")["enrolled"])
        self.assertNotIn("credential", self.request("status"))
        with self.assertRaisesRegex(ValueError, "Enroll Windows Hello"):
            self.request("get_api_key")
        self.request("enroll", credential="replacement", public_key="replacement-public")
        state = self.request("status")
        self.assertEqual(state["credential"], "replacement")
        self.assertFalse(state["settings"]["default_device_bound"])
        self.assertTrue(state["has_api_key"])
        self.assertEqual(self.db.execute("SELECT failures FROM attempts").fetchone(), (2,))
        self.assertEqual(self.db.execute("SELECT cover FROM covers").fetchone(), ("fixture cover",))

    def test_randomized_stego_and_natural_wav(self):
        # 1. Verify DWT BMP, Spatial BMP, PNG conversion, WAV, and FLAC conversion round-trip
        bmp_dwt = core.render_scenic_bmp(self.raw, theme="sunset", width=128, height=128, use_dwt=True)
        self.assertEqual(core.extract_media_payload(bmp_dwt), self.raw)
        png_dwt = media.convert_image(bmp_dwt, "PNG")
        self.assertEqual(core.extract_media_payload(png_dwt), self.raw)

        bmp_lsb = core.render_scenic_bmp(self.raw, theme="emerald", width=128, height=128, use_dwt=False)
        self.assertEqual(core.extract_media_payload(bmp_lsb), self.raw)
        bmp_fractal = core.build_bmp_carrier(self.raw, width=128, height=128, style_seed=1)
        self.assertEqual(core.extract_media_payload(bmp_fractal), self.raw)

        wav_data = core.build_wav_carrier(self.raw, duration_sec=1.5, sample_rate=22050)
        self.assertEqual(core.extract_media_payload(wav_data), self.raw)
        flac_data = media.convert_audio(wav_data, "FLAC")
        self.assertEqual(core.extract_media_payload(flac_data), self.raw)

        # 2. Verify fixed "DWT1" and sequential "SENC" headers do NOT exist in sequential LSBs
        def pack_sequential_bits(bits_list):
            out = bytearray(len(bits_list) // 8)
            for i in range(len(out) * 8):
                out[i // 8] |= (bits_list[i] & 1) << (7 - (i % 8))
            return bytes(out)

        offset = struct.unpack("<I", bmp_dwt[10:14])[0]
        w, h = struct.unpack("<II", bmp_dwt[18:26])
        row_size = (w * 3 + 3) & ~3
        pixels = bytearray()
        for y in range(h):
            pixels.extend(bmp_dwt[offset + y * row_size : offset + y * row_size + w * 3])
        green = [[pixels[(y * w + x) * 3 + 1] for x in range(w)] for y in range(h)]
        fwd = core.iwt2d_forward(green, w, h)
        lh_bits = [fwd[y][x] & 1 for y in range(h // 2) for x in range(w // 2, w)]
        seq_dwt_bytes = pack_sequential_bits(lh_bits)
        self.assertNotIn(b"DWT1", seq_dwt_bytes)
        self.assertNotIn(b"SENC", seq_dwt_bytes)

        offset_lsb = struct.unpack("<I", bmp_lsb[10:14])[0]
        seq_lsb_bytes = pack_sequential_bits([b & 1 for b in bmp_lsb[offset_lsb:]])
        self.assertNotIn(b"SENC", seq_lsb_bytes)

        # 3. Verify randomized positions scatter across the carrier and differ between runs
        bmp_lsb_2 = core.render_scenic_bmp(self.raw, theme="emerald", width=128, height=128, use_dwt=False)
        self.assertNotEqual(bmp_lsb, bmp_lsb_2)
        cover_bmp = core.render_scenic_bmp(b"", theme="emerald", width=128, height=128, use_dwt=False)
        diff_indices = [i for i in range(offset_lsb, len(bmp_lsb)) if bmp_lsb[i] != cover_bmp[i]]
        self.assertGreater(max(diff_indices), (len(bmp_lsb) - offset_lsb) * 0.8)

        # 4. Verify build_wav_carrier uses natural multi-harmonic audio rather than a 440 Hz pure sine wave
        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            n_frames = wf.getnframes()
            samples = struct.unpack(f"<{n_frames}h", wf.readframes(n_frames))
        # A 440 Hz pure sine wave at 22050 Hz has period ~50.11 samples and concentrates >95% energy at 440 Hz.
        total_energy = sum(s * s for s in samples)
        re_440 = sum(s * math.cos(2 * math.pi * 440 * i / 22050) for i, s in enumerate(samples))
        im_440 = sum(s * math.sin(2 * math.pi * 440 * i / 22050) for i, s in enumerate(samples))
        power_440_ratio = (2.0 * (re_440 * re_440 + im_440 * im_440) / max(1, n_frames)) / max(1, total_energy)
        self.assertLess(power_440_ratio, 0.25)

    def test_header_obfuscation_block_padding_and_entropy(self):
        with patch.object(crypto, "SERVICE_CONTEXT", True):
            # 1. Verify 256-byte block length padding hides exact plaintext length
            env_short, _ = crypto.seal("A", self.password, crypto.SINGLE)
            env_medium, _ = crypto.seal("B" * 200, self.password, crypto.SINGLE)
            env_next_block, _ = crypto.seal("C" * 300, self.password, crypto.SINGLE)
            self.assertEqual(len(env_short), crypto.HEADER.size + 256 + crypto.TAG_BYTES)
            self.assertEqual(len(env_short), len(env_medium))
            self.assertEqual(len(env_next_block), crypto.HEADER.size + 512 + crypto.TAG_BYTES)
            self.assertEqual(crypto.unseal(crypto.parse_envelope(env_short), self.password)[0], "A")
            self.assertEqual(crypto.unseal(crypto.parse_envelope(env_medium), self.password)[0], "B" * 200)
            self.assertEqual(crypto.unseal(crypto.parse_envelope(env_next_block), self.password)[0], "C" * 300)

            # 2. Verify SENC/SENP header stripping & XOR masking in wire format, Text Armor, and Camouflage Text
            wire_single = crypto.pack_wire_carrier(env_short)
            self.assertNotIn(b"SENC", wire_single)
            self.assertNotIn(b"SENP", wire_single)
            self.assertEqual(crypto.unpack_wire_carrier(wire_single), env_short)

            armor_text = workflow._armor(env_short)
            armor_match = workflow._ARMOR.fullmatch(armor_text)
            armor_bytes = base64.b64decode("".join(armor_match[1].split()))
            self.assertNotIn(b"SENC", armor_bytes)
            self.assertEqual(workflow.read_carriers(armor_text)[0].data, env_short)

            _, cam_parts = crypto.seal("Hidden poem secret", self.password, crypto.CAMOUFLAGE)
            templates = ["Amber leaves drift slowly above the quiet river" for _ in range(24)]
            poem = workflow._encode_camouflage(cam_parts[0], templates)
            self.assertEqual(workflow.read_carriers(poem)[0].data, cam_parts[0])

            # 3. Verify passphrase entropy estimation and Portable mode 128-bit guidance
            gen_pwd = crypto.generate_passphrase()
            bits, rating = crypto.estimate_passphrase_entropy(gen_pwd)
            self.assertEqual(bits, 128.0)
            self.assertEqual(rating, "Very Strong")
            low_summary = crypto.format_passphrase_strength("aaaaaaaaaaaaaaa", portable=True)
            self.assertIn("128-bit random passphrase recommended for Portable mode", low_summary)
            strong_summary = crypto.format_passphrase_strength(gen_pwd, portable=True)
            self.assertIn("128 bits (Very Strong)", strong_summary)
            self.assertNotIn("recommended", strong_summary)


if __name__ == "__main__":
    unittest.main()
