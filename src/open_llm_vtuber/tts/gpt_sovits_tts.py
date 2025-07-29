import re
import requests
from loguru import logger
from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    def __init__(
        self,
        api_url: str = "http://127.0.0.1:9880/",
        text_lang: str = "en",
        ref_audio_path: str = "C:\\Users\\lolly\\OneDrive\\Desktop\\Projects\\WaifuBeetle2\\character_files\\main_sample.wav",
        prompt_lang: str = "en",
        prompt_text: str = "This is a sample voice for you to just get started with because it sounds kind of cute but just make sure this doesn't have long silences.",
        text_split_method: str = "cut5",
        batch_size: str = "1",
        media_type: str = "wav",
        streaming_mode: str = "true",
    ):
        self.api_url = api_url
        self.text_lang = text_lang
        self.ref_audio_path = ref_audio_path
        self.prompt_lang = prompt_lang
        self.prompt_text = prompt_text
        self.text_split_method = text_split_method
        self.batch_size = batch_size
        self.media_type = media_type
        self.streaming_mode = streaming_mode

    def generate_audio(self, text, file_name_no_ext=None):
        print(f"Recieved text: {text}")
        file_name = self.generate_cache_file_name(file_name_no_ext, self.media_type)
        cleaned_text = re.sub(r"\[.*?\]", "", text)

        payload = {
        "refer_wav_path":   self.ref_audio_path,
        "prompt_text":      self.prompt_text,
        "prompt_language":  self.prompt_lang,
        "text":             cleaned_text,
        "text_language":    self.text_lang,
    }

        resp = requests.post(self.api_url, json=payload)
        resp.raise_for_status()

        if resp.status_code == 200:
            with open(file_name, "wb") as audio_file:
                audio_file.write(resp.content)
            return file_name
        else:
            logger.critical(
                f"Error: Failed to generate audio. Status code: {resp.status_code}"
            )
            return None
