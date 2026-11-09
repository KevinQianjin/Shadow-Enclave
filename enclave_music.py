"""Deterministic, independently arranged audio carrier themes."""

import math
from dataclasses import dataclass

import numpy as np

CYCLE = 6.5


@dataclass(frozen=True)
class Note:
    start: float
    duration: float
    pitch: int
    voice: str
    level: float = .24
    lead: bool = True


def melody(voice, pitches, starts, duration, level=.24):
    return [Note(start, duration, pitch, voice, level) for pitch, start in zip(pitches, starts, strict=True)]


def chord(voice, pitches, start, duration, level=.12):
    return [Note(start, duration, pitch, voice, level, False) for pitch in pitches]


def beat(voice, starts, duration, level):
    return [Note(start, duration, 0, voice, level, False) for start in starts]


def piano_arpeggios():
    track = []
    for i, chord_notes in enumerate(((48, 60, 64, 67), (45, 57, 60, 64), (41, 53, 57, 60), (43, 55, 59, 62))):
        start = .07+i*1.58
        track += melody("piano", (*chord_notes, chord_notes[-1]+12), [start+j*.26 for j in range(5)], 1.22)
        track += chord("strings", chord_notes[:2], start+.42, 1.12, .045)
    return track


def zen_plucks():
    return (melody("pluck", (62, 69, 76, 74, 64, 67, 81, 76), (.06,.57,1.43,1.98,2.88,3.72,4.6,5.54), 1.18)
            + chord("flute",(50,),.13,2.25,.075) + chord("flute",(55,),3.55,2.7,.06)
            + beat("wood", (.86, 3.48, 5.67), .16, .10))


def synthwave():
    track=[]
    for i, root in enumerate((45, 41, 48, 43)):
        start=i*1.59
        track += chord("synth",(root+12,root+19,root+24),start+.10,1.4,.095)
        track += [Note(start+j*.198,.185,root+(12 if j%2 else 0),"bass",.25,False) for j in range(8)]
        track += melody("synth",(root+24,root+31,root+27),(start+.29,start+.84,start+1.27),.25,.17)
    return track+beat("kick",(0,.8,1.59,2.39,3.18,3.97,4.77,5.56),.27,.4)+beat("hat",np.arange(.39,6.45,.4),.11,.085)


def music_box():
    return (melody("bell",(84,81,79,76,72,74,77,81,86,84,79,76),
                    (.03,.25,.53,1.02,1.44,2.13,2.43,3.01,3.86,4.11,4.54,5.49),1.4,.25)
            + chord("bell",(60,67),.15,2.36,.10) + chord("bell",(57,64),2.15,2.35,.08)
            + chord("bell",(55,62),4.2,2.14,.09))


def celtic_flute():
    track = melody("flute", (62,66,67,69,74,72,69,67,66,62,64,67,69,74),
                   (.04,.38,.62,.88,1.23,1.69,2.01,2.19,2.43,2.88,3.35,3.87,4.46,5.18),.37,.31)
    track += chord("drone",(38,45),0,3.0,.11) + chord("drone",(43,50),3.04,3.4,.085)
    return track+beat("wood",(0,.96,1.91,2.87,3.82,4.78,5.73),.21,.16)


def jazz_trio():
    track = []
    bassline=(43,47,50,53,41,45,48,50,46,48,50,53,43,46,47,50)
    track += [Note(.05+.397*i,.35,pitch,"bass",.23,False) for i,pitch in enumerate(bassline)]
    for tones, starts in (((53,59,62,69),(.27,1.32)), ((51,57,60,67),(1.91,2.55)),
                          ((52,58,62,67),(3.24,4.12)), ((53,58,62,67),(4.88,5.54))):
        for start in starts:
            track += chord("piano", tones, start, .43, .10)
    track += melody("piano",(74,70,67,69,72,74,77,74,71),(.12,.64,1.16,1.96,2.47,3.24,3.84,4.79,5.42),.32,.20)
    return track+beat("brush",(.41,1.18,1.99,2.75,3.58,4.35,5.16,5.94),.18,.13)


def lofi_rain():
    track = []
    for i, tones in enumerate(((45,57,60,64,67),(41,53,57,60,64),(48,55,59,64,67),(43,55,59,62,65))):
        track += chord("electric",tones,.09+i*1.61,1.46,.115)
    track += melody("electric",(72,71,67,64,69),(.53,1.14,2.26,3.95,5.39),.64,.15)
    return (track+beat("kick",(.06,1.22,1.68,2.90,3.27,4.84,5.71),.23,.33)
            +beat("brush",(.80,2.42,4.02,5.63),.20,.19)+beat("rain",(0,),6.5,.05))


def mountain_droplets():
    return (beat("rain",(0,),6.5,.14)
            +melody("drop",(86,79,91,83,88,75,81,95,79),(.08,.36,.85,1.41,2.36,2.51,3.67,4.22,5.38),.8,.24)
            +chord("flute",(53,60),1.9,2.8,.045)+chord("flute",(50,57),4.01,2.4,.05))


def forest():
    return (beat("wind",(0,),6.5,.19)
            + melody("flute",(67,71,74,71,64,67),(.33,.82,1.47,2.11,4.16,5.25),.79,.23)
            + melody("chirp",(89,93,91,87,91,84),(.12,.67,2.68,2.86,3.8,5.8),.17,.11)
            + chord("drone",(43,),.15,3.4,.06))


def ocean_bells():
    return (beat("surf",(0,),6.5,.3)
            +melody("bell",(60,67,72,57,64),(.28,1.83,2.57,4.05,5.61),2.45,.20)
            +chord("drone",(36,48),0,3.85,.07)
            +chord("drone",(33,45),3.2,3.28,.095))


def space_atmosphere():
    return (chord("space",(38,45,50,57),0,4.2,.14) + chord("space",(35,42,47,54),2.68,3.8,.12)
            + melody("space",(77,79,74,81),(.48,1.73,3.45,4.75),1.86,.15)
            + chord("drone",(26,),.09,6.3,.09))


def sparse_percussion():
    return (beat("kick",(.03,.76,1.19,1.96,2.63,3.21,3.86,4.53,5.27,5.87),.23,.42)
            + beat("wood",(.45,1.03,1.62,2.19,2.88,3.48,4.07,4.95,5.48,6.18),.13,.25)
            + beat("hat",(.28,.93,1.42,2.11,2.51,2.96,3.63,4.25,4.71,5.1,5.66,6.13),.09,.11)
            + melody("drop",(49,52,47),(.69,2.81,5.26),.34,.16))


def cinematic_strings():
    return (chord("strings",(45,52,57),0,2.64,.18) + chord("strings",(41,53,57,60),1.76,2.84,.17)
            + chord("strings",(48,55,60,64),3.48,2.95,.16)
            + melody("strings",(69,72,76,79,77),(.10,1.10,2.06,3.57,5.10),1.66,.24)
            + beat("kick",(0,3.67,5.71),.44,.13))


ARRANGEMENTS = {
    "ambient": piano_arpeggios, "zen": zen_plucks, "cyber": synthwave, "lullaby": music_box,
    "celtic": celtic_flute, "jazz": jazz_trio, "lofi": lofi_rain, "rain": mountain_droplets,
    "forest": forest, "ocean_bells": ocean_bells, "space": space_atmosphere,
    "percussion": sparse_percussion, "strings": cinematic_strings,
}

# Attack, release shape, decay, and harmonics differ by instrument.
TONES = {
    "piano": (.012, .24, 3.3, (1, .31, .14, .04)),
    "electric": (.018, .32, 1.75, (1, .12, .34, .04)),
    "pluck": (.006, .16, 5.8, (1, .37, .30, .18)),
    "flute": (.11, .25, .67, (1, .09, .025, .01)),
    "bell": (.004, .43, 2.6, (1, .22, .36, .26)),
    "synth": (.012, .22, 1.5, (1, .46, .33, .20)),
    "strings": (.31, .27, .10, (1, .35, .33, .17)),
    "space": (.42, .38, .20, (1, .12, .25, .04)),
    "drone": (.30, .32, .20, (1, .12, .045, 0)),
    "bass": (.010, .32, 1.54, (1, .26, .15, 0)),
    "drop": (.003, .38, 3.7, (1, .07, .29, .05)),
    "chirp": (.012, .25, .8, (1, .09, .035, 0)),
}


def _noise(t, rng):
    noise = rng.uniform(-1, 1, size=len(t))
    slow = np.interp(t, np.linspace(0, t[-1], max(3, int(t[-1]*230)+1)),
                     rng.uniform(-1, 1, size=max(3, int(t[-1]*230)+1)))
    return noise, slow


def _voice(voice, pitch, t, duration, sample_rate, rng):
    if voice == "kick":
        phase=math.tau*(42*t+44*(1-np.exp(-29*t))/29)
        return np.sin(phase)*np.exp(-12*t)*np.minimum(1,t*350)
    if voice in {"wood","hat","brush","rain","wind","surf"}:
        noise, slow = _noise(t, rng)
        if voice == "wood":
            return (.45*noise+.55*np.sin(math.tau*595*t))*np.exp(-29*t)
        if voice == "hat":
            return (noise-np.roll(noise,1))*.50*np.exp(-34*t)
        if voice == "brush":
            return (.6*noise+.4*slow)*np.exp(-14*t)
        if voice == "rain":
            return (.65*noise+.35*slow)*(.72+.28*np.sin(math.tau*.72*t)**2)
        if voice == "wind":
            return slow*(.62+.38*np.sin(math.tau*.37*t))
        return (.28*noise+.72*slow)*(.20+.80*np.sin(math.tau*.21*t+.47)**2)

    attack, release, decay, harmonics = TONES[voice]
    env = np.minimum(t/attack,1)*np.minimum((duration-t)/max(.006, duration*release),1)
    env *= np.exp(-decay*t/duration)
    freq=440*2**((pitch-69)/12)
    phase=math.tau*freq*t
    if voice in {"flute","strings","space","electric"}:
        phase += (.21 if voice=="strings" else .13)*np.sin(math.tau*4.9*t)
    elif voice in {"chirp","drop"}:
        phase += math.tau*freq*.009*(1-np.exp(-16*t))
    ratios=(1,2.01,2.73,4.18) if voice in {"bell","drop"} else (1,2,3,4)
    tone = sum(gain * np.sin(ratio * phase) for ratio, gain in zip(ratios, harmonics, strict=True)
               if freq * ratio < sample_rate * 0.48)
    return env*tone/max(1,sum(harmonics)*.78)


def render_pcm(style, sample_rate, sample_count):
    arrangement = ARRANGEMENTS[style.theme]()
    duration = sample_count/sample_rate
    cycle = CYCLE/style.tempo
    rng = np.random.default_rng(sum(ord(c)*(i+1) for i,c in enumerate(style.theme)))
    result=np.zeros(sample_count,dtype=np.float64)
    for offset_index in range(math.ceil(duration/cycle)):
        offset = offset_index*cycle
        notes = arrangement
        if style.pulse:
            notes = [*notes, *beat("kick",(.04,1.61,3.21,4.81),.29,style.pulse*.24)]
        for note in notes:
            length = note.duration/style.tempo
            start = round((offset+note.start/style.tempo)*sample_rate)
            count = min(max(1,round(length*sample_rate)),sample_count-start)
            if count <= 0:
                continue
            voice = style.timbre if note.lead and style.timbre != "native" else note.voice
            pitch=note.pitch+style.pitch
            wave=_voice(voice,pitch,np.arange(count)/sample_rate,length,sample_rate,rng)*note.level
            result[start:start+count]+=wave
            if style.room:
                echo = start + round((.11+.22*style.room)*sample_rate)
                size = min(count,sample_count-echo)
                if size>0:
                    result[echo:echo+size]+=wave[:size]*style.room*.42
    fade=min(sample_count//4,round(sample_rate*.08))
    if fade:
        result[-fade:]*=np.linspace(1,0,fade)
        result[:fade]*=np.linspace(0,1,fade)
    return np.rint(np.tanh(result*1.5)*30000).astype("<i2").tobytes()
