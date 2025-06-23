import csv
import json
import os
import subprocess
import time
import logging
from pydub import AudioSegment
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# Load configuration
def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_config()

log_file = config.get("log_file", "process.log")

# Clear log file at start
with open(log_file, "w", encoding="utf-8") as f:
    f.write("")

# Configure logging
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

output_dir = os.path.expanduser(config.get("output_dir", "./output"))
os.makedirs(output_dir, exist_ok=True)

def timestamp_to_ms(ts):
    try:
        min_, sec = map(int, ts.strip().split(":"))
        return (min_ * 60 + sec) * 1000
    except Exception as e:
        logging.error(f"Invalid start time '{ts}': {e}")
        return None

def download_song(query, temp_path, retries=3, delay=5):
    command = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", config.get("audio_format", "mp3"),
        "-o", temp_path,
        f"ytsearch1:{query}"
    ]
    for attempt in range(1, retries + 1):
        try:
            logging.info(f"Attempting download: '{query}', attempt {attempt}")
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError as e:
            logging.warning(f"Download attempt {attempt} failed: {e}")
            if attempt < retries:
                time.sleep(delay)
    return False

def trim_song(input_path, output_path, start_time, duration_sec=30, normalize=True):
    start_ms = timestamp_to_ms(start_time)
    if start_ms is None:
        logging.error(f"Invalid start time: {start_time}")
        return False
    try:
        audio = AudioSegment.from_file(input_path)
        end_ms = start_ms + duration_sec * 1000
        if end_ms > len(audio):
            end_ms = len(audio)
        snippet = audio[start_ms:end_ms]

        # Ensure stereo output
        snippet = snippet.set_channels(2)

        if normalize:
            target_dBFS = config.get("target_dBFS", -20.0)
            snippet = match_target_amplitude(snippet, target_dBFS)

        # Apply fade in/out if enabled in config
        if config.get("fade_in", False):
            fade_in_duration = config.get("fade_in_duration_ms", 0)
            if fade_in_duration > 0:
                snippet = snippet.fade_in(fade_in_duration)
        if config.get("fade_out", False):
            fade_out_duration = config.get("fade_out_duration_ms", 0)
            if fade_out_duration > 0:
                snippet = snippet.fade_out(fade_out_duration)

        # Use format from config (default: mp3)
        audio_format = config.get("audio_format", "mp3")
        bitrate = config.get("audio_bitrate", None)
        sample_rate = config.get("sample_rate", None)

        export_kwargs = {}
        if bitrate and audio_format.lower() == "mp3":
            export_kwargs["bitrate"] = bitrate
        if sample_rate:
            export_kwargs["parameters"] = ["-ar", str(sample_rate)]

        snippet.export(output_path, format=audio_format, **export_kwargs)
        return True
    except Exception as e:
        logging.error(f"Error while trimming: {e}")
        return False

def match_target_amplitude(sound, target_dBFS):
    change_in_dBFS = target_dBFS - sound.dBFS
    return sound.apply_gain(change_in_dBFS)


# New function: process a single row
def process_row(row):
    firstName = row.get(config["csv_columns"]["name"], "").strip()
    lastName = row.get(config["csv_columns"]["surname"], "").strip()
    song = row.get(config["csv_columns"]["song"], "").strip()
    startTime = row.get(config["csv_columns"]["start_time"], "").strip()

    if not firstName or not lastName or not song or not startTime:
        logging.info(f"Skipping incomplete row: {row}")
        return

    filename_base = f"{lastName}, {firstName}"
    audio_format = config.get("audio_format", "mp3")
    final_path = os.path.join(output_dir, f"{filename_base}.{audio_format}")
    temp_path = os.path.join(output_dir, f"{filename_base}_full.%(ext)s")

    if os.path.exists(final_path) and not config.get("overwrite_existing_files", False):
        logging.info(f"File already exists and overwrite is False: {final_path}")
        return

    logging.info(f"Starting processing: {firstName} {lastName}, Song: {song}, Start time: {startTime}")

    if not download_song(song, temp_path,
                         retries=config.get("max_download_retries", 3),
                         delay=config.get("retry_delay_seconds", 5)):
        logging.error(f"Download failed for {song}")
        print(f"❌ Download failed for {song}")
        return

    downloaded_file = temp_path.replace("%(ext)s", audio_format)

    if not trim_song(downloaded_file, final_path, startTime,
                     duration_sec=config.get("default_clip_duration_seconds", 30),
                     normalize=config.get("normalize_audio", True)):
        logging.error(f"Trimming failed for {final_path}")
        print(f"❌ Trimming failed for {final_path}")
        return

    if os.path.exists(downloaded_file):
        os.remove(downloaded_file)

    logging.info(f"Successfully saved: {final_path}")

def process_csv(csv_path):
    delimiter = config.get("csv_delimiter", ";")
    with open(csv_path, newline='', encoding="utf-8") as file:
        reader = csv.DictReader(file, delimiter=delimiter)
        rows = list(reader)

    progress_bar = tqdm(total=len(rows), desc="Processing", unit="track")

    max_workers = config.get("parallel_workers", 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_row, row) for row in rows]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error during parallel processing: {e}")
            finally:
                progress_bar.update(1)
    progress_bar.close()

if __name__ == "__main__":
    csv_file = config.get("csv_file", "testdata.csv")
    process_csv(csv_file)