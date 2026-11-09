"""朗读：和川听同一套。edge-tts（微软神经网络语音，免费、不用 Key）合成句子，按文本缓存成 mp3。
连不上微软时退回系统自带的离线语音：macOS 用 say，Windows 用 SAPI（System.Speech），输出 WAV。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from .store import AUDIO_DIR

RATE = 24000  # 离线语音输出 24kHz 单声道
SPEAKABLE = re.compile(r"[A-Za-z0-9一-鿿]")

# 设置页可选的声音（edge-tts 的名字 → 显示名）。默认和川听一样用 Aria
VOICES = {
    "en-US-AriaNeural": "Aria · 美式女声（默认，和川听一样）",
    "en-US-JennyNeural": "Jenny · 美式女声",
    "en-US-EmmaNeural": "Emma · 美式女声",
    "en-US-GuyNeural": "Guy · 美式男声",
    "en-US-AndrewNeural": "Andrew · 美式男声",
    "en-GB-SoniaNeural": "Sonia · 英式女声",
    "en-GB-RyanNeural": "Ryan · 英式男声",
    "en-AU-NatashaNeural": "Natasha · 澳式女声",
}
DEFAULT_VOICE = "en-US-AriaNeural"


class TTSError(RuntimeError):
    pass


def speakable(text: str) -> bool:
    return bool(SPEAKABLE.search(text or ""))


def rate_str(rate: int) -> str:
    """-5 → "-5%"（edge-tts 的语速写法）"""
    return f"{int(rate):+d}%"


_sem = asyncio.Semaphore(6)


def _cache_path(text: str, voice: str, rate: str, ext: str) -> Path:
    h = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:20]
    return AUDIO_DIR / h[:2] / f"{h}.{ext}"


async def synth(text: str, voice: str = DEFAULT_VOICE, rate: str = "-5%") -> Path:
    """返回音频文件路径（mp3；离线兜底时是 wav）"""
    text = re.sub(r"\s+", " ", text).strip()
    mp3 = _cache_path(text, voice, rate, "mp3")
    if mp3.exists() and mp3.stat().st_size > 0:
        return mp3
    wav = _cache_path(text, "offline:" + voice, rate, "wav")
    if wav.exists() and wav.stat().st_size > 44:
        return wav
    mp3.parent.mkdir(parents=True, exist_ok=True)
    async with _sem:
        if os.environ.get("CHUANWEN_OFFLINE_TTS"):  # 自测用：不连微软
            return await asyncio.to_thread(offline_tts, text, voice, rate, wav)
        try:
            import edge_tts

            tmp = mp3.with_suffix(".part")
            await asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate).save(str(tmp)), timeout=30)
            if tmp.stat().st_size == 0:
                raise RuntimeError("empty audio")
            tmp.rename(mp3)
            return mp3
        except Exception:
            try:
                return await asyncio.to_thread(offline_tts, text, voice, rate, wav)
            except Exception as e:
                raise TTSError("语音合成失败：连不上微软朗读，本机离线朗读也没成功") from e


# Windows：用 PowerShell 调系统自带的 System.Speech。文字和路径走环境变量，不拼进命令，避免引号和注入问题
_SAPI_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$want = $env:CHUANWEN_CULTURE
$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $want } | Select-Object -First 1
if (-not $v) { $v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name.StartsWith($want.Substring(0, 2)) } | Select-Object -First 1 }
if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($env:CHUANWEN_OUT, $fmt)
$s.Speak($env:CHUANWEN_TEXT)
$s.Dispose()
"""


def offline_tts(text: str, voice: str, rate: str, out: Path) -> Path:
    """系统自带的离线语音，输出 24kHz 单声道 WAV"""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.wav")
    if sys.platform == "darwin":
        mac_voice = "Daniel" if voice.startswith("en-GB") else "Samantha"
        wpm = str(int(175 * (1 + int(rate.rstrip("%")) / 100)))
        subprocess.run(["say", "-v", mac_voice, "-r", wpm, "--file-format=WAVE", f"--data-format=LEI16@{RATE}", "-o", str(tmp), text],
                       check=True, capture_output=True, timeout=60)
    elif sys.platform == "win32":
        culture = "en-GB" if voice.startswith("en-GB") else "en-US"
        env = {**os.environ, "CHUANWEN_TEXT": text, "CHUANWEN_OUT": str(tmp), "CHUANWEN_CULTURE": culture}
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SAPI_PS],
                       check=True, capture_output=True, timeout=60, env=env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        raise RuntimeError("这个系统没有可用的离线语音")
    if not tmp.exists() or tmp.stat().st_size <= 44:
        raise RuntimeError("离线语音没有生成音频")
    tmp.replace(out)
    return out
