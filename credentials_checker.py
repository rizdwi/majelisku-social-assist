import requests
from typing import Dict, Any

def validate_threads_credentials(token: str, user_id: str = "me") -> Dict[str, Any]:
    token = token.strip()
    if not token:
        return {"valid": False, "message": "Token Threads belum diisi."}

    if len(token) < 20:
        return {
            "valid": False,
            "message": f"Panjang token terlalu pendek ({len(token)} karakter). Token resmi Meta biasanya lebih dari 30 karakter."
        }

    user_id = user_id.strip() or "me"
    url = f"https://graph.threads.net/v1.0/{user_id}"
    params = {
        "fields": "id,username",
        "access_token": token
    }
    try:
        resp = requests.get(url, params=params, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            username = data.get("username", "Pengguna")
            uid = data.get("id", user_id)
            return {
                "valid": True,
                "provider": "Threads Graph API",
                "message": f"Koneksi Threads berhasil! Terhubung sebagai @{username} (ID: {uid})."
            }
        else:
            try:
                err_data = resp.json()
                err_msg = err_data.get("error", {}).get("message", resp.text)
            except Exception:
                err_msg = resp.text
            return {
                "valid": False,
                "provider": "Threads Graph API",
                "message": f"Gagal autentikasi Threads (HTTP {resp.status_code}): {err_msg}"
            }
    except requests.exceptions.Timeout:
        return {"valid": False, "message": "Waktu permintaan ke Threads API habis (timeout 8 detik)."}
    except Exception as e:
        return {"valid": False, "message": f"Gagal menghubungi server Threads: {str(e)}"}

def validate_ai_credentials(api_key: str, model: str = "claude-3-5-sonnet-20241022", base_url: str = "") -> Dict[str, Any]:
    api_key = api_key.strip()
    if not api_key:
        return {"valid": False, "message": "API Key AI belum diisi (mode fallback offline aktif)."}

    model = model.strip() or "claude-3-5-sonnet-20241022"
    base_url = base_url.strip()

    # Deteksi provider berdasarkan format token atau URL
    is_anthropic = (api_key.startswith("sk-ant-") or "claude" in model.lower()) and not base_url

    if is_anthropic:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}]
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                return {
                    "valid": True,
                    "provider": "Anthropic",
                    "model": model,
                    "message": f"Koneksi Anthropic berhasil! Model '{model}' aktif dan siap digunakan."
                }
            else:
                try:
                    err_msg = resp.json().get("error", {}).get("message", resp.text)
                except Exception:
                    err_msg = resp.text
                return {
                    "valid": False,
                    "provider": "Anthropic",
                    "message": f"Autentikasi Anthropic gagal (HTTP {resp.status_code}): {err_msg}"
                }
        except requests.exceptions.Timeout:
            return {"valid": False, "message": "Waktu permintaan ke Anthropic habis (timeout 8 detik)."}
        except Exception as e:
            return {"valid": False, "message": f"Gagal menghubungi Anthropic: {str(e)}"}
    else:
        # OpenAI / OpenRouter / Custom compatible API
        target_base = base_url.rstrip("/") if base_url else "https://api.openai.com/v1"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        # Cek ringan ke endpoint /models jika didukung
        try:
            resp = requests.get(f"{target_base}/models", headers=headers, timeout=8)
            if resp.status_code == 200:
                provider_name = "OpenRouter" if "openrouter" in target_base.lower() else "OpenAI / Kompatibel"
                return {
                    "valid": True,
                    "provider": provider_name,
                    "model": model,
                    "message": f"Koneksi {provider_name} berhasil! Kunci API valid dan siap digunakan."
                }
        except Exception:
            pass

        # Fallback tes pesan 1-token ke /chat/completions
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1
        }
        try:
            resp = requests.post(f"{target_base}/chat/completions", headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                provider_name = "OpenRouter" if "openrouter" in target_base.lower() else "OpenAI / Kompatibel"
                return {
                    "valid": True,
                    "provider": provider_name,
                    "model": model,
                    "message": f"Koneksi {provider_name} berhasil! Model '{model}' aktif dan merespons."
                }
            else:
                try:
                    err_msg = resp.json().get("error", {}).get("message", resp.text)
                except Exception:
                    err_msg = resp.text
                return {
                    "valid": False,
                    "provider": "OpenAI / Custom API",
                    "message": f"Gagal verifikasi API AI (HTTP {resp.status_code}): {err_msg}"
                }
        except requests.exceptions.Timeout:
            return {"valid": False, "message": "Waktu permintaan ke server AI habis (timeout 8 detik)."}
        except Exception as e:
            return {"valid": False, "message": f"Gagal menghubungi server AI: {str(e)}"}
