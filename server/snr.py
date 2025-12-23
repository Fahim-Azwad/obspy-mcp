
import numpy as np

def snr(trace):
    data = trace.data.astype(float)
    noise = data[: len(data)//10]
    signal = data[len(data)//4: len(data)//2]
    return signal.std() / max(noise.std(), 1e-6)
