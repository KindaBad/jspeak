"""Lightweight audio helpers shared by both daemons. Pure stdlib so it adds no
build dependencies on either platform."""
import array


def trim_silence(pcm, rate, frame_ms=20, threshold=320, pad_ms=120):
    """Trim leading/trailing near-silence from 16-bit mono PCM bytes.

    Conservative on purpose: it only removes clearly-silent head/tail regions
    (mean absolute amplitude below `threshold`) and keeps `pad_ms` of padding so
    no quiet speech onset/offset is clipped. If the whole clip looks silent it is
    returned unchanged (let the min-record guard decide), and the input is always
    returned unchanged on any error.
    """
    try:
        samples = array.array("h")
        samples.frombytes(pcm)
        n = len(samples)
        if n == 0:
            return pcm
        frame = max(1, int(rate * frame_ms / 1000))
        pad = int(rate * pad_ms / 1000)

        def loud(i):
            seg = samples[i:i + frame]
            if not seg:
                return False
            return (sum(abs(s) for s in seg) / len(seg)) >= threshold

        first = None
        for i in range(0, n, frame):
            if loud(i):
                first = i
                break
        if first is None:
            return pcm                      # all quiet; don't gut the clip

        last = first
        for i in range(n - frame if n > frame else 0, -1, -frame):
            if i < 0:
                break
            if loud(i):
                last = i + frame
                break

        start = max(0, first - pad)
        end = min(n, last + pad)
        if start <= 0 and end >= n:
            return pcm
        return samples[start:end].tobytes()
    except Exception:
        return pcm
