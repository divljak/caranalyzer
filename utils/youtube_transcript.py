"""
YouTube Transcript Analyzer
Fetches transcripts from YouTube videos and extracts key takeaways.
"""
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    # Handle direct video IDs (11 chars)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
        return url

    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # youtube.com/watch?v=ID
    if hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            if "v" in qs:
                return qs["v"][0]
        # youtube.com/embed/ID or youtube.com/v/ID
        match = re.match(r"^/(embed|v)/([a-zA-Z0-9_-]{11})", parsed.path)
        if match:
            return match.group(2)
        # youtube.com/shorts/ID
        match = re.match(r"^/shorts/([a-zA-Z0-9_-]{11})", parsed.path)
        if match:
            return match.group(1)

    # youtu.be/ID
    if hostname in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/")
        if re.match(r'^[a-zA-Z0-9_-]{11}$', video_id):
            return video_id

    return None


def fetch_transcript(video_id: str, languages: Optional[list] = None) -> dict:
    """
    Fetch the transcript for a YouTube video.

    Returns dict with:
        - video_id: str
        - segments: list of {text, start, duration}
        - full_text: str (plain text of the entire transcript)
        - duration_seconds: float (total video duration estimate)
    """
    if languages is None:
        languages = ["en", "en-US", "en-GB"]

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    except Exception as e:
        raise ValueError(f"Could not access transcripts for video {video_id}: {e}")

    # Try to find transcript in preferred languages, then fall back to auto-generated
    transcript = None
    try:
        transcript = transcript_list.find_transcript(languages)
    except Exception:
        # Try to find any transcript and translate it
        try:
            for t in transcript_list:
                if t.is_translatable:
                    transcript = t.translate("en")
                    break
        except Exception:
            pass

    if transcript is None:
        # Last resort: grab the first available transcript
        try:
            for t in transcript_list:
                transcript = t
                break
        except Exception:
            raise ValueError(f"No transcripts available for video {video_id}")

    segments = transcript.fetch()

    formatter = TextFormatter()
    full_text = formatter.format_transcript(segments)

    # Estimate total duration from last segment
    if segments:
        last = segments[-1]
        duration_seconds = last.get("start", 0) + last.get("duration", 0)
    else:
        duration_seconds = 0.0

    return {
        "video_id": video_id,
        "segments": [
            {"text": s.get("text", ""), "start": s.get("start", 0), "duration": s.get("duration", 0)}
            for s in segments
        ],
        "full_text": full_text,
        "duration_seconds": duration_seconds,
    }


def chunk_text(text: str, max_chunk_size: int = 3000) -> list:
    """Split text into chunks at sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 > max_chunk_size and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            current_chunk = current_chunk + " " + sentence if current_chunk else sentence

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]


def analyze_transcript(full_text: str, duration_seconds: float) -> dict:
    """
    Analyze transcript text and extract key takeaways using heuristic NLP.

    Returns dict with:
        - summary: str
        - key_takeaways: list of str
        - topics: list of str
        - word_count: int
        - estimated_reading_time_min: float
    """
    words = full_text.split()
    word_count = len(words)
    reading_time = round(word_count / 200, 1)  # ~200 wpm average reading speed

    # Extract sentences
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    # Score sentences by importance heuristics
    scored_sentences = []
    importance_signals = [
        r'\b(important|key|crucial|essential|must|need to|remember|note that)\b',
        r'\b(first|second|third|finally|in conclusion|to summarize|takeaway)\b',
        r'\b(the main|the biggest|the most|number one|top)\b',
        r'\b(tip|trick|strategy|technique|method|approach|step)\b',
        r'\b(because|therefore|as a result|this means|so basically)\b',
        r'\b(don\'t|never|always|avoid|make sure)\b',
    ]

    for i, sentence in enumerate(sentences):
        score = 0.0
        lower = sentence.lower()

        # Importance keyword signals
        for pattern in importance_signals:
            if re.search(pattern, lower):
                score += 2.0

        # Position bias: first and last 20% of sentences are often more important
        position_ratio = i / max(len(sentences), 1)
        if position_ratio < 0.15 or position_ratio > 0.85:
            score += 1.5

        # Longer sentences tend to carry more information (up to a point)
        word_len = len(sentence.split())
        if 10 <= word_len <= 40:
            score += 1.0

        # Sentences with numbers often contain facts
        if re.search(r'\d+', sentence):
            score += 0.5

        scored_sentences.append((sentence, score))

    # Sort by score and pick top takeaways
    scored_sentences.sort(key=lambda x: x[1], reverse=True)

    # Deduplicate similar sentences
    key_takeaways = []
    seen_words = set()
    for sentence, score in scored_sentences:
        sentence_words = set(sentence.lower().split())
        # Skip if too similar to an already selected sentence
        overlap = len(sentence_words & seen_words) / max(len(sentence_words), 1)
        if overlap < 0.5:
            key_takeaways.append(sentence)
            seen_words.update(sentence_words)
        if len(key_takeaways) >= 8:
            break

    # Extract likely topics via frequent noun-like capitalized words & repeated phrases
    word_freq = {}
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above", "below",
        "between", "out", "off", "over", "under", "again", "further", "then",
        "once", "here", "there", "when", "where", "why", "how", "all", "both",
        "each", "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very", "just",
        "don", "now", "and", "but", "or", "if", "it", "its", "this", "that",
        "these", "those", "i", "me", "my", "we", "our", "you", "your", "he",
        "him", "his", "she", "her", "they", "them", "their", "what", "which",
        "who", "whom", "about", "up", "like", "also", "really", "going",
        "think", "know", "get", "got", "thing", "things", "say", "said",
        "one", "two", "much", "many", "well", "right", "because", "even",
        "still", "way", "want", "look", "make", "go", "see", "come", "take",
        "good", "new", "people", "time", "back", "lot", "kind", "actually",
    }

    for word in words:
        cleaned = re.sub(r'[^a-zA-Z]', '', word).lower()
        if cleaned and len(cleaned) > 3 and cleaned not in stop_words:
            word_freq[cleaned] = word_freq.get(cleaned, 0) + 1

    # Topics: most frequent meaningful words
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    topics = [word for word, count in sorted_words[:10] if count >= 3]

    # Build a summary from top 3 takeaways
    summary_sentences = key_takeaways[:3]
    summary = " ".join(summary_sentences) if summary_sentences else "No summary could be generated."

    duration_min = round(duration_seconds / 60, 1)

    return {
        "summary": summary,
        "key_takeaways": key_takeaways,
        "topics": topics,
        "word_count": word_count,
        "estimated_reading_time_min": reading_time,
        "video_duration_min": duration_min,
    }
