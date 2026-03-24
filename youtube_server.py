"""
Standalone YouTube Transcript Analyzer server.
Run with: python youtube_server.py
Then open http://localhost:8000/youtube on your phone.
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from pathlib import Path

from utils.youtube_transcript import extract_video_id, fetch_transcript, analyze_transcript

app = FastAPI(title="YouTube Transcript Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class TranscriptRequest(BaseModel):
    url: str


class FullAnalysisResponse(BaseModel):
    video_id: str
    transcript_preview: str
    summary: str
    key_takeaways: List[str]
    topics: List[str]
    word_count: int
    estimated_reading_time_min: float
    video_duration_min: float


@app.get("/")
async def root():
    return FileResponse(str(static_dir / "youtube.html"))


@app.get("/youtube")
async def youtube_page():
    return FileResponse(str(static_dir / "youtube.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/youtube/analyze", response_model=FullAnalysisResponse)
async def analyze_youtube_video(request: TranscriptRequest):
    video_id = extract_video_id(request.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")

    try:
        data = fetch_transcript(video_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching transcript: {e}")

    full_text = data["full_text"]
    if not full_text or not full_text.strip():
        raise HTTPException(status_code=422, detail="Transcript is empty — video may not have captions")

    analysis = analyze_transcript(full_text, data["duration_seconds"])
    preview = full_text[:500] + ("..." if len(full_text) > 500 else "")

    return FullAnalysisResponse(
        video_id=data["video_id"],
        transcript_preview=preview,
        summary=analysis["summary"],
        key_takeaways=analysis["key_takeaways"],
        topics=analysis["topics"],
        word_count=analysis["word_count"],
        estimated_reading_time_min=analysis["estimated_reading_time_min"],
        video_duration_min=analysis["video_duration_min"],
    )


if __name__ == "__main__":
    import uvicorn
    print("\n  YouTube Transcript Analyzer running!")
    print("  Open http://localhost:8000/youtube\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
