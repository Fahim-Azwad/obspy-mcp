
from obspy.clients.fdsn import Client

def client(provider: str):
    return Client(provider)

def get_events(provider, kwargs):
    return client(provider).get_events(**kwargs)

def get_stations(provider, kwargs):
    return client(provider).get_stations(**kwargs)

def get_waveforms(provider, kwargs):
    return client(provider).get_waveforms(**kwargs)
