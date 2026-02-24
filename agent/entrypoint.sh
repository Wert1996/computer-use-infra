#!/bin/bash
set -euo pipefail

DISPLAY_NUM=99
export DISPLAY=:${DISPLAY_NUM}

PRESIGNED_POST_DATA="${PRESIGNED_POST_DATA:-}"
OUTPUT_DIR="/output"
mkdir -p "$OUTPUT_DIR"

cleanup() {
    if [ -n "${FFMPEG_PID:-}" ] && kill -0 "$FFMPEG_PID" 2>/dev/null; then
        kill -SIGTERM "$FFMPEG_PID" 2>/dev/null || true
        wait "$FFMPEG_PID" 2>/dev/null || true
    fi
    if [ -n "${XVFB_PID:-}" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo '{"step":0,"action":"starting_xvfb"}'
Xvfb :${DISPLAY_NUM} -screen 0 1024x768x24 &
XVFB_PID=$!

TIMEOUT=30
ELAPSED=0
while [ ! -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; do
    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo '{"step":0,"action":"xvfb_timeout","error":"Xvfb failed to start within 30s"}'
        exit 1
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done
echo '{"step":0,"action":"xvfb_ready","elapsed_seconds":'$ELAPSED'}'

if [ -n "$PRESIGNED_POST_DATA" ]; then
    echo '{"step":0,"action":"starting_ffmpeg"}'
    ffmpeg -y -f x11grab -video_size 1024x768 -framerate 5 -i :${DISPLAY_NUM} \
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
        -f mp4 -movflags frag_keyframe+empty_moov \
        "${OUTPUT_DIR}/recording.mp4" 2>/dev/null &
    FFMPEG_PID=$!
    sleep 1
fi

echo '{"step":0,"action":"starting_agent"}'
AGENT_EXIT=0
python3 /app/agent.py || AGENT_EXIT=$?

if [ -n "${FFMPEG_PID:-}" ] && kill -0 "$FFMPEG_PID" 2>/dev/null; then
    kill -SIGTERM "$FFMPEG_PID" 2>/dev/null || true
    wait "$FFMPEG_PID" 2>/dev/null || true
fi

if [ -n "$PRESIGNED_POST_DATA" ]; then
    echo '{"step":0,"action":"uploading_recording"}'
    if [ -f "${OUTPUT_DIR}/recording.mp4" ]; then
        python3 -c "from agent import upload_file; upload_file('/output/recording.mp4', 'recording.mp4')" || true
    fi
fi

echo '{"step":0,"action":"entrypoint_done","agent_exit_code":'$AGENT_EXIT'}'
exit $AGENT_EXIT
