
def recommend_pre_filt(sampling_rate):
    nyq = sampling_rate / 2.0
    return [0.01 * nyq, 0.02 * nyq, 0.8 * nyq, 0.9 * nyq]
