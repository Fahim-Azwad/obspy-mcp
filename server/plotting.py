
import matplotlib.pyplot as plt

def plot_stream(stream, out):
    fig = stream.plot(show=False)
    fig.savefig(out, dpi=150)
    plt.close(fig)
