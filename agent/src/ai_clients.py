import logging
import os

import boto3
from dotenv import load_dotenv
from livekit.plugins.cerebras import LLM as CerebrasLLM
from livekit.plugins import aws, groq, openai, silero
from livekit.plugins.deepgram import STT as DeepgramSTT
from livekit.plugins.sarvam import STT as SarvamSTT, TTS as SarvamTTS
from livekit.plugins.elevenlabs import TTS as ElevenLabsTTS, VoiceSettings
#from sarvam_tts_patched import TTS as SarvamTTS
from livekit.plugins.cerebras import LLM as CerebrasLLM

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

_root_logger = logging.getLogger("services")


def _require_env(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable '{key}' is not set.")
    return value


def _mask(secret: str, visible: int = 4) -> str:
    return "****" if len(secret) <= visible else secret[:visible] + "****"


# --- Bedrock singleton (lazy loaded) ---

_bedrock_client = None


def _build_bedrock_client(logger=None) -> boto3.client:
    _log = logger or _root_logger
    try:
        region = os.getenv("BEDROCK_REGION", "us-east-1")
        access_key = _require_env("AWS_ACCESS_KEY_ID")
        secret_key = _require_env("AWS_SECRET_ACCESS_KEY")
        client = boto3.client(
            "bedrock-agent-runtime",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        _log.info("Bedrock client initialized | region=%s | key_id=%s", region, _mask(access_key))
        return client
    except RuntimeError as exc:
        _log.error(
            "Bedrock client failed — missing env var | error=%s: %s",
            type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "Bedrock client failed | error=%s: %s",
            type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("Bedrock client initialization failed.") from exc


def get_bedrock_agent(logger=None) -> boto3.client:
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = _build_bedrock_client(logger=logger)
    return _bedrock_client


# --- STT ---

_DEEPGRAM_SUPPORTED_MODELS = {"nova-2", "nova-3", "whisper-large", "base"}


def create_stt(language: str = "hi", model: str = "nova-3", logger=None) -> DeepgramSTT:
    _log = logger or _root_logger
    if model not in _DEEPGRAM_SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported Deepgram model: '{model}'. "
            f"Supported: {', '.join(sorted(_DEEPGRAM_SUPPORTED_MODELS))}"
        )
    try:
        api_key = _require_env("DEEPGRAM_API_KEY")
        _log.info(
            "STT init | provider=deepgram | lang=%s | model=%s | key=%s",
            language, model, _mask(api_key)
        )
        stt = DeepgramSTT(language=language, model=model, interim_results=True, punctuate=True)
        _log.info("STT created | provider=deepgram | lang=%s | model=%s", language, model)
        return stt
    except RuntimeError as exc:
        _log.error(
            "STT failed — missing env var | provider=deepgram | lang=%s | model=%s | error=%s: %s",
            language, model, type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "STT failed | provider=deepgram | lang=%s | model=%s | error=%s: %s",
            language, model, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("Deepgram STT initialization failed.") from exc


_SARVAM_STT_SUPPORTED_LANGUAGES = {
    "as-IN",
    "bn-IN",
    "brx-IN",
    "doi-IN",
    "en-IN",
    "gu-IN",
    "hi-IN",
    "kn-IN",
    "kok-IN",
    "ks-IN",
    "mai-IN",
    "ml-IN",
    "mni-IN",
    "mr-IN",
    "ne-IN",
    "od-IN",
    "pa-IN",
    "sa-IN",
    "sat-IN",
    "sd-IN",
    "ta-IN",
    "te-IN",
    "unknown",
    "ur-IN",
}
_SARVAM_STT_SUPPORTED_MODELS = {"saarika:v2.5", "saaras:v2.5", "saaras:v3"}
_SARVAM_STT_SUPPORTED_MODES = {"transcribe", "translate", "verbatim", "translit", "codemix"}
_SARVAM_STT_LANGUAGE_ALIASES = {
    "en": "en-IN",
    "hi": "hi-IN",
    "ml": "ml-IN",
}


def create_sarvam_stt(
    language: str = "hi-IN",
    model: str = "saaras:v3",
    mode: str = "transcribe",
    sample_rate: int = 16000,
    logger=None,
) -> SarvamSTT:
    _log = logger or _root_logger
    language = _SARVAM_STT_LANGUAGE_ALIASES.get(language, language)
    if language not in _SARVAM_STT_SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported Sarvam STT language: '{language}'. "
            f"Supported: {', '.join(sorted(_SARVAM_STT_SUPPORTED_LANGUAGES))}"
        )
    if model not in _SARVAM_STT_SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported Sarvam STT model: '{model}'. "
            f"Supported: {', '.join(sorted(_SARVAM_STT_SUPPORTED_MODELS))}"
        )
    if mode not in _SARVAM_STT_SUPPORTED_MODES:
        raise ValueError(
            f"Unsupported Sarvam STT mode: '{mode}'. "
            f"Supported: {', '.join(sorted(_SARVAM_STT_SUPPORTED_MODES))}"
        )
    if model != "saaras:v3" and mode != "transcribe":
        raise ValueError(
            f"Sarvam STT mode '{mode}' is only supported with model 'saaras:v3'."
        )
    if sample_rate <= 0:
        raise ValueError(
            f"Sarvam STT sample_rate must be greater than 0, got {sample_rate}."
        )
    try:
        api_key = _require_env("SARVAM_API_KEY")
        _log.info(
            "STT init | provider=sarvam | lang=%s | model=%s | mode=%s | sample_rate=%d | key=%s",
            language, model, mode, sample_rate, _mask(api_key)
        )
        stt = SarvamSTT(
            language=language,
            model=model,
            mode=mode,
            sample_rate=sample_rate,
            api_key=api_key,
        )
        _log.info(
            "STT created | provider=sarvam | lang=%s | model=%s | mode=%s",
            language, model, mode
        )
        return stt
    except RuntimeError as exc:
        _log.error(
            "STT failed â€” missing env var | provider=sarvam | lang=%s | model=%s | mode=%s | error=%s: %s",
            language, model, mode, type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "STT failed | provider=sarvam | lang=%s | model=%s | mode=%s | error=%s: %s",
            language, model, mode, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("Sarvam STT initialization failed.") from exc


# --- VAD ---

def create_vad(activation_threshold: float = 0.75, logger=None) -> silero.VAD:
    _log = logger or _root_logger
    if not 0.0 < activation_threshold <= 1.0:
        raise ValueError(f"activation_threshold must be in (0.0, 1.0], got {activation_threshold}.")
    try:
        _log.info("VAD init | provider=silero | threshold=%.2f", activation_threshold)
        vad = silero.VAD.load(activation_threshold=activation_threshold)
        _log.info("VAD loaded | provider=silero | threshold=%.2f", activation_threshold)
        return vad
    except Exception as exc:
        _log.error(
            "VAD failed | provider=silero | threshold=%.2f | error=%s: %s",
            activation_threshold, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("Silero VAD initialization failed.") from exc


# --- TTS ---

def create_elevenlabs_tts(
    voice_id: str = "iWNf11sz1GrUE4ppxTOL",
    model: str = "eleven_turbo_v2_5",
    logger=None,
) -> ElevenLabsTTS:
    _log = logger or _root_logger
    try:
        api_key = _require_env("ELEVENLABS_API_KEY")
        _log.info(
            "TTS init | provider=elevenlabs | voice_id=%s | model=%s | key=%s",
            voice_id, model, _mask(api_key)
        )
        tts = ElevenLabsTTS(
            voice_id=voice_id,
            model=model,
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                style=0.0,
                speed=0.8,
                use_speaker_boost=True,
            ),
        )
        _log.info("TTS created | provider=elevenlabs | voice_id=%s | model=%s", voice_id, model)
        return tts
    except RuntimeError as exc:
        _log.error(
            "TTS failed — missing env var | provider=elevenlabs | voice_id=%s | model=%s | error=%s: %s",
            voice_id, model, type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "TTS failed | provider=elevenlabs | voice_id=%s | model=%s | error=%s: %s",
            voice_id, model, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("ElevenLabs TTS initialization failed.") from exc






_SARVAM_SUPPORTED_LANGUAGES = {"en-IN", "hi-IN", "ml-IN"}
_SARVAM_SUPPORTED_VOICES = {"priya", "shubh"}

def create_sarvam_tts(
    language: str,
    voice: str,
    model: str = "bulbul:v3",
    pace: float = 1.0,
    temperature: float = 0.6,
    speech_sample_rate: int = 24000,
    logger=None,
) -> SarvamTTS:
    _log = logger or _root_logger
    if language not in _SARVAM_SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported language: '{language}'. "
            f"Supported: {', '.join(sorted(_SARVAM_SUPPORTED_LANGUAGES))}"
        )
    if voice not in _SARVAM_SUPPORTED_VOICES:
        raise ValueError(
            f"Unsupported voice: '{voice}'. "
            f"Supported: {', '.join(sorted(_SARVAM_SUPPORTED_VOICES))}"
        )
    try:
        api_key = _require_env("SARVAM_API_KEY")
        _log.info(
            "TTS init | provider=sarvam | lang=%s | voice=%s | model=%s | key=%s",
            language, voice, model, _mask(api_key)
        )
        tts = SarvamTTS(
            target_language_code=language,
            model=model,
            speaker=voice,
            pace=pace,
            temperature=temperature,
            speech_sample_rate=speech_sample_rate,
        )
        _log.info(
            "TTS created | provider=sarvam | lang=%s | voice=%s | model=%s | pace=%.2f",
            language, voice, model, pace,
        )
        return tts
    except RuntimeError as exc:
        _log.error(
            "TTS failed — missing env var | provider=sarvam | lang=%s | voice=%s | error=%s: %s",
            language, voice, type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "TTS failed | provider=sarvam | lang=%s | voice=%s | error=%s: %s",
            language, voice, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("Sarvam TTS initialization failed.") from exc


# --- LLM ---

_BEDROCK_DEFAULT_MODEL_ARN = os.getenv(
    "BEDROCK_MODEL_ARN",
    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0",
)


def create_aws_llm(model: str = _BEDROCK_DEFAULT_MODEL_ARN, temperature: float = 0.9, logger=None) -> aws.LLM:
    _log = logger or _root_logger
    try:
        access_key = _require_env("AWS_ACCESS_KEY_ID")
        _require_env("AWS_SECRET_ACCESS_KEY")
        _log.info(
            "LLM init | provider=aws_bedrock | temperature=%.2f | key=%s",
            temperature, _mask(access_key)
        )
        llm = aws.LLM(model=model, temperature=temperature)
        _log.info("LLM created | provider=aws_bedrock | temperature=%.2f", temperature)
        return llm
    except RuntimeError as exc:
        _log.error(
            "LLM failed — missing env var | provider=aws_bedrock | error=%s: %s",
            type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "LLM failed | provider=aws_bedrock | temperature=%.2f | error=%s: %s",
            temperature, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("AWS Bedrock LLM initialization failed.") from exc


def create_groq_llm(
    model: str = "openai/gpt-oss-20b",
    temperature: float = 0.15,
    top_p: float = 0.9,
    max_tokens: int = 80,
    logger=None,
) -> groq.LLM:
    _log = logger or _root_logger
    try:
        api_key = _require_env("GROQ_API_KEY")
        _log.info(
            "LLM init | provider=groq | model=%s | temperature=%.2f | key=%s",
            model, temperature, _mask(api_key)
        )
        llm = groq.LLM(
            api_key=api_key,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            parallel_tool_calls=False,
        )
        _log.info(
            "LLM created | provider=groq | model=%s | temperature=%.2f | max_tokens=%d",
            model, temperature, max_tokens,
        )
        return llm
    except RuntimeError as exc:
        _log.error(
            "LLM failed — missing env var | provider=groq | model=%s | error=%s: %s",
            model, type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "LLM failed | provider=groq | model=%s | error=%s: %s",
            model, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("Groq LLM initialization failed.") from exc


def create_sambanova_llm(
    model: str = "Llama-4-Maverick-17B-128E-Instruct",
    temperature: float = 0.2,
    max_completion_tokens: int = 1000,
    tool_choice: str = "none",
    parallel_tool_calls: bool = False,
    logger=None,
):
    """
    Create a SambaNova LLM instance with proper logging and error handling.
    
    Args:
        model: Model name (default: Llama-4-Maverick-17B-128E-Instruct)
        temperature: Model temperature (default: 0.2)
        max_completion_tokens: Max tokens for completion (default: 1000)
        tool_choice: Tool choice strategy (default: "none")
        parallel_tool_calls: Allow parallel tool calls (default: False)
        logger: Logger instance (optional)
    
    Returns:
        LLM instance configured for SambaNova
    """
    _log = logger or _root_logger
    try:
        api_key = _require_env("SAMBANOVA_API_KEY")
        base_url = os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1")
        _log.info("LLM provider usage detected: sambanova")
        _log.info(
            "LLM init | provider=sambanova | model=%s | temperature=%.2f | key=%s",
            model, temperature, _mask(api_key)
        )
        llm = openai.LLM(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=temperature,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            max_completion_tokens=max_completion_tokens,
        )
        _log.info(
            "LLM created | provider=sambanova | model=%s | temperature=%.2f | max_tokens=%d",
            model, temperature, max_completion_tokens,
        )
        return llm
    except RuntimeError as exc:
        _log.error(
            "LLM failed — missing env var | provider=sambanova | model=%s | error=%s: %s",
            model, type(exc).__name__, str(exc)
        )
        raise
    except Exception as exc:
        _log.error(
            "LLM failed | provider=sambanova | model=%s | error=%s: %s",
            model, type(exc).__name__, str(exc)
        )
        _log.exception("Full traceback:")
        raise RuntimeError("SambaNova LLM initialization failed.") from exc




def create_cerebras_llm(
    model: str = "gpt-oss-120b",
    temperature: float = 0.7,
    tool_choice: str = "auto",
    parallel_tool_calls: bool = False,
    reasoning_effort: str = "medium",
    logger=None,
):
    _log = logger or _root_logger
    try:
        api_key = _require_env("CEREBRAS_API_KEY")
        _log.info(
            "LLM init | provider=cerebras | model=%s | temperature=%.2f | reasoning=%s | key=%s",
            model, temperature, reasoning_effort, _mask(api_key),
        )
        llm = CerebrasLLM(
            model=model,
            api_key=api_key,
            temperature=temperature,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_effort=reasoning_effort,
            gzip_compression=True,
            msgpack_encoding=False,
        )
        _log.info(
            "LLM created | provider=cerebras | model=%s | temperature=%.2f | reasoning=%s",
            model, temperature, reasoning_effort,
        )
        return llm
    except RuntimeError as exc:
        _log.error(
            "LLM failed — missing env var | provider=cerebras | model=%s | error=%s: %s",
            model, type(exc).__name__, str(exc),
        )
        raise
    except Exception as exc:
        _log.exception(
            "LLM failed | provider=cerebras | model=%s | error=%s: %s",
            model, type(exc).__name__, str(exc),
        )
        raise RuntimeError("Cerebras LLM initialization failed.") from exc
    
    
