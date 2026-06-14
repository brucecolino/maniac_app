### \# Audio



\## Domain Types



\### Audio Model



\- `AudioModel = "whisper-1" or "gpt-4o-transcribe" or "gpt-4o-mini-transcribe" or 2 more`



&#x20; - `"whisper-1"`



&#x20; - `"gpt-4o-transcribe"`



&#x20; - `"gpt-4o-mini-transcribe"`



&#x20; - `"gpt-4o-mini-transcribe-2025-12-15"`



&#x20; - `"gpt-4o-transcribe-diarize"`



\### Audio Response Format



\- `AudioResponseFormat = "json" or "text" or "srt" or 3 more`



&#x20; The format of the output, in one of these options: `json`, `text`, `srt`, `verbose\_json`, `vtt`, or `diarized\_json`. For `gpt-4o-transcribe` and `gpt-4o-mini-transcribe`, the only supported format is `json`. For `gpt-4o-transcribe-diarize`, the supported formats are `json`, `text`, and `diarized\_json`, with `diarized\_json` required to receive speaker annotations.



&#x20; - `"json"`



&#x20; - `"text"`



&#x20; - `"srt"`



&#x20; - `"verbose\_json"`



&#x20; - `"vtt"`



&#x20; - `"diarized\_json"`



\# Transcriptions



\## Create transcription



\*\*post\*\* `/audio/transcriptions`



Transcribes audio into the input language.



Returns a transcription object in `json`, `diarized\_json`, or `verbose\_json`

format, or a stream of transcript events.



\### Returns



\- `Transcription object { text, logprobs, usage }`



&#x20; Represents a transcription response returned by model, based on the provided input.



&#x20; - `text: string`



&#x20;   The transcribed text.



&#x20; - `logprobs: optional array of object { token, bytes, logprob }`



&#x20;   The log probabilities of the tokens in the transcription. Only returned with the models `gpt-4o-transcribe` and `gpt-4o-mini-transcribe` if `logprobs` is added to the `include` array.



&#x20;   - `token: optional string`



&#x20;     The token in the transcription.



&#x20;   - `bytes: optional array of number`



&#x20;     The bytes of the token.



&#x20;   - `logprob: optional number`



&#x20;     The log probability of the token.



&#x20; - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }  or object { seconds, type }`



&#x20;   Token usage statistics for the request.



&#x20;   - `TokenUsage object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;     Usage statistics for models billed by token usage.



&#x20;     - `input\_tokens: number`



&#x20;       Number of input tokens billed for this request.



&#x20;     - `output\_tokens: number`



&#x20;       Number of output tokens generated.



&#x20;     - `total\_tokens: number`



&#x20;       Total number of tokens used (input + output).



&#x20;     - `type: "tokens"`



&#x20;       The type of the usage object. Always `tokens` for this variant.



&#x20;       - `"tokens"`



&#x20;     - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;       Details about the input tokens billed for this request.



&#x20;       - `audio\_tokens: optional number`



&#x20;         Number of audio tokens billed for this request.



&#x20;       - `text\_tokens: optional number`



&#x20;         Number of text tokens billed for this request.



&#x20;   - `DurationUsage object { seconds, type }`



&#x20;     Usage statistics for models billed by audio input duration.



&#x20;     - `seconds: number`



&#x20;       Duration of the input audio in seconds.



&#x20;     - `type: "duration"`



&#x20;       The type of the usage object. Always `duration` for this variant.



&#x20;       - `"duration"`



\- `TranscriptionDiarized object { duration, segments, task, 2 more }`



&#x20; Represents a diarized transcription response returned by the model, including the combined transcript and speaker-segment annotations.



&#x20; - `duration: number`



&#x20;   Duration of the input audio in seconds.



&#x20; - `segments: array of TranscriptionDiarizedSegment`



&#x20;   Segments of the transcript annotated with timestamps and speaker labels.



&#x20;   - `id: string`



&#x20;     Unique identifier for the segment.



&#x20;   - `end: number`



&#x20;     End timestamp of the segment in seconds.



&#x20;   - `speaker: string`



&#x20;     Speaker label for this segment. When known speakers are provided, the label matches `known\_speaker\_names\[]`. Otherwise speakers are labeled sequentially using capital letters (`A`, `B`, ...).



&#x20;   - `start: number`



&#x20;     Start timestamp of the segment in seconds.



&#x20;   - `text: string`



&#x20;     Transcript text for this segment.



&#x20;   - `type: "transcript.text.segment"`



&#x20;     The type of the segment. Always `transcript.text.segment`.



&#x20;     - `"transcript.text.segment"`



&#x20; - `task: "transcribe"`



&#x20;   The type of task that was run. Always `transcribe`.



&#x20;   - `"transcribe"`



&#x20; - `text: string`



&#x20;   The concatenated transcript text for the entire audio input.



&#x20; - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }  or object { seconds, type }`



&#x20;   Token or duration usage statistics for the request.



&#x20;   - `Tokens object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;     Usage statistics for models billed by token usage.



&#x20;     - `input\_tokens: number`



&#x20;       Number of input tokens billed for this request.



&#x20;     - `output\_tokens: number`



&#x20;       Number of output tokens generated.



&#x20;     - `total\_tokens: number`



&#x20;       Total number of tokens used (input + output).



&#x20;     - `type: "tokens"`



&#x20;       The type of the usage object. Always `tokens` for this variant.



&#x20;       - `"tokens"`



&#x20;     - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;       Details about the input tokens billed for this request.



&#x20;       - `audio\_tokens: optional number`



&#x20;         Number of audio tokens billed for this request.



&#x20;       - `text\_tokens: optional number`



&#x20;         Number of text tokens billed for this request.



&#x20;   - `Duration object { seconds, type }`



&#x20;     Usage statistics for models billed by audio input duration.



&#x20;     - `seconds: number`



&#x20;       Duration of the input audio in seconds.



&#x20;     - `type: "duration"`



&#x20;       The type of the usage object. Always `duration` for this variant.



&#x20;       - `"duration"`



\- `TranscriptionVerbose object { duration, language, text, 3 more }`



&#x20; Represents a verbose json transcription response returned by model, based on the provided input.



&#x20; - `duration: number`



&#x20;   The duration of the input audio.



&#x20; - `language: string`



&#x20;   The language of the input audio.



&#x20; - `text: string`



&#x20;   The transcribed text.



&#x20; - `segments: optional array of TranscriptionSegment`



&#x20;   Segments of the transcribed text and their corresponding details.



&#x20;   - `id: number`



&#x20;     Unique identifier of the segment.



&#x20;   - `avg\_logprob: number`



&#x20;     Average logprob of the segment. If the value is lower than -1, consider the logprobs failed.



&#x20;   - `compression\_ratio: number`



&#x20;     Compression ratio of the segment. If the value is greater than 2.4, consider the compression failed.



&#x20;   - `end: number`



&#x20;     End time of the segment in seconds.



&#x20;   - `no\_speech\_prob: number`



&#x20;     Probability of no speech in the segment. If the value is higher than 1.0 and the `avg\_logprob` is below -1, consider this segment silent.



&#x20;   - `seek: number`



&#x20;     Seek offset of the segment.



&#x20;   - `start: number`



&#x20;     Start time of the segment in seconds.



&#x20;   - `temperature: number`



&#x20;     Temperature parameter used for generating the segment.



&#x20;   - `text: string`



&#x20;     Text content of the segment.



&#x20;   - `tokens: array of number`



&#x20;     Array of token IDs for the text content.



&#x20; - `usage: optional object { seconds, type }`



&#x20;   Usage statistics for models billed by audio input duration.



&#x20;   - `seconds: number`



&#x20;     Duration of the input audio in seconds.



&#x20;   - `type: "duration"`



&#x20;     The type of the usage object. Always `duration` for this variant.



&#x20;     - `"duration"`



&#x20; - `words: optional array of TranscriptionWord`



&#x20;   Extracted words and their corresponding timestamps.



&#x20;   - `end: number`



&#x20;     End time of the word in seconds.



&#x20;   - `start: number`



&#x20;     Start time of the word in seconds.



&#x20;   - `word: string`



&#x20;     The text content of the word.



\### Example



```http

curl https://api.openai.com/v1/audio/transcriptions \\

&#x20;   -H 'Content-Type: multipart/form-data' \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20;   -F 'file=@/path/to/file' \\

&#x20;   -F model=gpt-4o-transcribe

```



\#### Response



```json

{

&#x20; "text": "text",

&#x20; "logprobs": \[

&#x20;   {

&#x20;     "token": "token",

&#x20;     "bytes": \[

&#x20;       0

&#x20;     ],

&#x20;     "logprob": 0

&#x20;   }

&#x20; ],

&#x20; "usage": {

&#x20;   "input\_tokens": 0,

&#x20;   "output\_tokens": 0,

&#x20;   "total\_tokens": 0,

&#x20;   "type": "tokens",

&#x20;   "input\_token\_details": {

&#x20;     "audio\_tokens": 0,

&#x20;     "text\_tokens": 0

&#x20;   }

&#x20; }

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/transcriptions \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: multipart/form-data" \\

&#x20; -F file="@/path/to/file/audio.mp3" \\

&#x20; -F model="gpt-4o-transcribe"

```



\#### Response



```json

{

&#x20; "text": "Imagine the wildest idea that you've ever had, and you're curious about how it might scale to something that's a 100, a 1,000 times bigger. This is a place where you can get to do that.",

&#x20; "usage": {

&#x20;   "type": "tokens",

&#x20;   "input\_tokens": 14,

&#x20;   "input\_token\_details": {

&#x20;     "text\_tokens": 0,

&#x20;     "audio\_tokens": 14

&#x20;   },

&#x20;   "output\_tokens": 45,

&#x20;   "total\_tokens": 59

&#x20; }

}

```



\### Diarization



```http

curl https://api.openai.com/v1/audio/transcriptions \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: multipart/form-data" \\

&#x20; -F file="@/path/to/file/meeting.wav" \\

&#x20; -F model="gpt-4o-transcribe-diarize" \\

&#x20; -F response\_format="diarized\_json" \\

&#x20; -F chunking\_strategy=auto \\

&#x20; -F 'known\_speaker\_names\[]=agent' \\

&#x20; -F 'known\_speaker\_references\[]=data:audio/wav;base64,AAA...'

```



\#### Response



```json

{

&#x20; "task": "transcribe",

&#x20; "duration": 27.4,

&#x20; "text": "Agent: Thanks for calling OpenAI support.\\nA: Hi, I'm trying to enable diarization.\\nAgent: Happy to walk you through the steps.",

&#x20; "segments": \[

&#x20;   {

&#x20;     "type": "transcript.text.segment",

&#x20;     "id": "seg\_001",

&#x20;     "start": 0.0,

&#x20;     "end": 4.7,

&#x20;     "text": "Thanks for calling OpenAI support.",

&#x20;     "speaker": "agent"

&#x20;   },

&#x20;   {

&#x20;     "type": "transcript.text.segment",

&#x20;     "id": "seg\_002",

&#x20;     "start": 4.7,

&#x20;     "end": 11.8,

&#x20;     "text": "Hi, I'm trying to enable diarization.",

&#x20;     "speaker": "A"

&#x20;   },

&#x20;   {

&#x20;     "type": "transcript.text.segment",

&#x20;     "id": "seg\_003",

&#x20;     "start": 12.1,

&#x20;     "end": 18.5,

&#x20;     "text": "Happy to walk you through the steps.",

&#x20;     "speaker": "agent"

&#x20;   }

&#x20; ],

&#x20; "usage": {

&#x20;   "type": "duration",

&#x20;   "seconds": 27

&#x20; }

}

```



\### Streaming



```http

curl https://api.openai.com/v1/audio/transcriptions \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: multipart/form-data" \\

&#x20; -F file="@/path/to/file/audio.mp3" \\

&#x20; -F model="gpt-4o-mini-transcribe" \\

&#x20; -F stream=true

```



\#### Response



```json

data: {"type":"transcript.text.delta","delta":"I","logprobs":\[{"token":"I","logprob":-0.00007588794,"bytes":\[73]}]}



data: {"type":"transcript.text.delta","delta":" see","logprobs":\[{"token":" see","logprob":-3.1281633e-7,"bytes":\[32,115,101,101]}]}



data: {"type":"transcript.text.delta","delta":" skies","logprobs":\[{"token":" skies","logprob":-2.3392786e-6,"bytes":\[32,115,107,105,101,115]}]}



data: {"type":"transcript.text.delta","delta":" of","logprobs":\[{"token":" of","logprob":-3.1281633e-7,"bytes":\[32,111,102]}]}



data: {"type":"transcript.text.delta","delta":" blue","logprobs":\[{"token":" blue","logprob":-1.0280384e-6,"bytes":\[32,98,108,117,101]}]}



data: {"type":"transcript.text.delta","delta":" and","logprobs":\[{"token":" and","logprob":-0.0005108566,"bytes":\[32,97,110,100]}]}



data: {"type":"transcript.text.delta","delta":" clouds","logprobs":\[{"token":" clouds","logprob":-1.9361265e-7,"bytes":\[32,99,108,111,117,100,115]}]}



data: {"type":"transcript.text.delta","delta":" of","logprobs":\[{"token":" of","logprob":-1.9361265e-7,"bytes":\[32,111,102]}]}



data: {"type":"transcript.text.delta","delta":" white","logprobs":\[{"token":" white","logprob":-7.89631e-7,"bytes":\[32,119,104,105,116,101]}]}



data: {"type":"transcript.text.delta","delta":",","logprobs":\[{"token":",","logprob":-0.0014890312,"bytes":\[44]}]}



data: {"type":"transcript.text.delta","delta":" the","logprobs":\[{"token":" the","logprob":-0.0110956915,"bytes":\[32,116,104,101]}]}



data: {"type":"transcript.text.delta","delta":" bright","logprobs":\[{"token":" bright","logprob":0.0,"bytes":\[32,98,114,105,103,104,116]}]}



data: {"type":"transcript.text.delta","delta":" blessed","logprobs":\[{"token":" blessed","logprob":-0.000045848617,"bytes":\[32,98,108,101,115,115,101,100]}]}



data: {"type":"transcript.text.delta","delta":" days","logprobs":\[{"token":" days","logprob":-0.000010802739,"bytes":\[32,100,97,121,115]}]}



data: {"type":"transcript.text.delta","delta":",","logprobs":\[{"token":",","logprob":-0.00001700133,"bytes":\[44]}]}



data: {"type":"transcript.text.delta","delta":" the","logprobs":\[{"token":" the","logprob":-0.0000118755715,"bytes":\[32,116,104,101]}]}



data: {"type":"transcript.text.delta","delta":" dark","logprobs":\[{"token":" dark","logprob":-5.5122365e-7,"bytes":\[32,100,97,114,107]}]}



data: {"type":"transcript.text.delta","delta":" sacred","logprobs":\[{"token":" sacred","logprob":-5.4385737e-6,"bytes":\[32,115,97,99,114,101,100]}]}



data: {"type":"transcript.text.delta","delta":" nights","logprobs":\[{"token":" nights","logprob":-4.00813e-6,"bytes":\[32,110,105,103,104,116,115]}]}



data: {"type":"transcript.text.delta","delta":",","logprobs":\[{"token":",","logprob":-0.0036910512,"bytes":\[44]}]}



data: {"type":"transcript.text.delta","delta":" and","logprobs":\[{"token":" and","logprob":-0.0031903093,"bytes":\[32,97,110,100]}]}



data: {"type":"transcript.text.delta","delta":" I","logprobs":\[{"token":" I","logprob":-1.504853e-6,"bytes":\[32,73]}]}



data: {"type":"transcript.text.delta","delta":" think","logprobs":\[{"token":" think","logprob":-4.3202e-7,"bytes":\[32,116,104,105,110,107]}]}



data: {"type":"transcript.text.delta","delta":" to","logprobs":\[{"token":" to","logprob":-1.9361265e-7,"bytes":\[32,116,111]}]}



data: {"type":"transcript.text.delta","delta":" myself","logprobs":\[{"token":" myself","logprob":-1.7432603e-6,"bytes":\[32,109,121,115,101,108,102]}]}



data: {"type":"transcript.text.delta","delta":",","logprobs":\[{"token":",","logprob":-0.29254505,"bytes":\[44]}]}



data: {"type":"transcript.text.delta","delta":" what","logprobs":\[{"token":" what","logprob":-0.016815351,"bytes":\[32,119,104,97,116]}]}



data: {"type":"transcript.text.delta","delta":" a","logprobs":\[{"token":" a","logprob":-3.1281633e-7,"bytes":\[32,97]}]}



data: {"type":"transcript.text.delta","delta":" wonderful","logprobs":\[{"token":" wonderful","logprob":-2.1008714e-6,"bytes":\[32,119,111,110,100,101,114,102,117,108]}]}



data: {"type":"transcript.text.delta","delta":" world","logprobs":\[{"token":" world","logprob":-8.180258e-6,"bytes":\[32,119,111,114,108,100]}]}



data: {"type":"transcript.text.delta","delta":".","logprobs":\[{"token":".","logprob":-0.014231676,"bytes":\[46]}]}



data: {"type":"transcript.text.done","text":"I see skies of blue and clouds of white, the bright blessed days, the dark sacred nights, and I think to myself, what a wonderful world.","logprobs":\[{"token":"I","logprob":-0.00007588794,"bytes":\[73]},{"token":" see","logprob":-3.1281633e-7,"bytes":\[32,115,101,101]},{"token":" skies","logprob":-2.3392786e-6,"bytes":\[32,115,107,105,101,115]},{"token":" of","logprob":-3.1281633e-7,"bytes":\[32,111,102]},{"token":" blue","logprob":-1.0280384e-6,"bytes":\[32,98,108,117,101]},{"token":" and","logprob":-0.0005108566,"bytes":\[32,97,110,100]},{"token":" clouds","logprob":-1.9361265e-7,"bytes":\[32,99,108,111,117,100,115]},{"token":" of","logprob":-1.9361265e-7,"bytes":\[32,111,102]},{"token":" white","logprob":-7.89631e-7,"bytes":\[32,119,104,105,116,101]},{"token":",","logprob":-0.0014890312,"bytes":\[44]},{"token":" the","logprob":-0.0110956915,"bytes":\[32,116,104,101]},{"token":" bright","logprob":0.0,"bytes":\[32,98,114,105,103,104,116]},{"token":" blessed","logprob":-0.000045848617,"bytes":\[32,98,108,101,115,115,101,100]},{"token":" days","logprob":-0.000010802739,"bytes":\[32,100,97,121,115]},{"token":",","logprob":-0.00001700133,"bytes":\[44]},{"token":" the","logprob":-0.0000118755715,"bytes":\[32,116,104,101]},{"token":" dark","logprob":-5.5122365e-7,"bytes":\[32,100,97,114,107]},{"token":" sacred","logprob":-5.4385737e-6,"bytes":\[32,115,97,99,114,101,100]},{"token":" nights","logprob":-4.00813e-6,"bytes":\[32,110,105,103,104,116,115]},{"token":",","logprob":-0.0036910512,"bytes":\[44]},{"token":" and","logprob":-0.0031903093,"bytes":\[32,97,110,100]},{"token":" I","logprob":-1.504853e-6,"bytes":\[32,73]},{"token":" think","logprob":-4.3202e-7,"bytes":\[32,116,104,105,110,107]},{"token":" to","logprob":-1.9361265e-7,"bytes":\[32,116,111]},{"token":" myself","logprob":-1.7432603e-6,"bytes":\[32,109,121,115,101,108,102]},{"token":",","logprob":-0.29254505,"bytes":\[44]},{"token":" what","logprob":-0.016815351,"bytes":\[32,119,104,97,116]},{"token":" a","logprob":-3.1281633e-7,"bytes":\[32,97]},{"token":" wonderful","logprob":-2.1008714e-6,"bytes":\[32,119,111,110,100,101,114,102,117,108]},{"token":" world","logprob":-8.180258e-6,"bytes":\[32,119,111,114,108,100]},{"token":".","logprob":-0.014231676,"bytes":\[46]}],"usage":{"input\_tokens":14,"input\_token\_details":{"text\_tokens":0,"audio\_tokens":14},"output\_tokens":45,"total\_tokens":59}}

```



\### Logprobs



```http

curl https://api.openai.com/v1/audio/transcriptions \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: multipart/form-data" \\

&#x20; -F file="@/path/to/file/audio.mp3" \\

&#x20; -F "include\[]=logprobs" \\

&#x20; -F model="gpt-4o-transcribe" \\

&#x20; -F response\_format="json"

```



\#### Response



```json

{

&#x20; "text": "Hey, my knee is hurting and I want to see the doctor tomorrow ideally.",

&#x20; "logprobs": \[

&#x20;   { "token": "Hey", "logprob": -1.0415299, "bytes": \[72, 101, 121] },

&#x20;   { "token": ",", "logprob": -9.805982e-5, "bytes": \[44] },

&#x20;   { "token": " my", "logprob": -0.00229799, "bytes": \[32, 109, 121] },

&#x20;   {

&#x20;     "token": " knee",

&#x20;     "logprob": -4.7159858e-5,

&#x20;     "bytes": \[32, 107, 110, 101, 101]

&#x20;   },

&#x20;   { "token": " is", "logprob": -0.043909557, "bytes": \[32, 105, 115] },

&#x20;   {

&#x20;     "token": " hurting",

&#x20;     "logprob": -1.1041146e-5,

&#x20;     "bytes": \[32, 104, 117, 114, 116, 105, 110, 103]

&#x20;   },

&#x20;   { "token": " and", "logprob": -0.011076359, "bytes": \[32, 97, 110, 100] },

&#x20;   { "token": " I", "logprob": -5.3193703e-6, "bytes": \[32, 73] },

&#x20;   {

&#x20;     "token": " want",

&#x20;     "logprob": -0.0017156356,

&#x20;     "bytes": \[32, 119, 97, 110, 116]

&#x20;   },

&#x20;   { "token": " to", "logprob": -7.89631e-7, "bytes": \[32, 116, 111] },

&#x20;   { "token": " see", "logprob": -5.5122365e-7, "bytes": \[32, 115, 101, 101] },

&#x20;   { "token": " the", "logprob": -0.0040786397, "bytes": \[32, 116, 104, 101] },

&#x20;   {

&#x20;     "token": " doctor",

&#x20;     "logprob": -2.3392786e-6,

&#x20;     "bytes": \[32, 100, 111, 99, 116, 111, 114]

&#x20;   },

&#x20;   {

&#x20;     "token": " tomorrow",

&#x20;     "logprob": -7.89631e-7,

&#x20;     "bytes": \[32, 116, 111, 109, 111, 114, 114, 111, 119]

&#x20;   },

&#x20;   {

&#x20;     "token": " ideally",

&#x20;     "logprob": -0.5800861,

&#x20;     "bytes": \[32, 105, 100, 101, 97, 108, 108, 121]

&#x20;   },

&#x20;   { "token": ".", "logprob": -0.00011093382, "bytes": \[46] }

&#x20; ],

&#x20; "usage": {

&#x20;   "type": "tokens",

&#x20;   "input\_tokens": 14,

&#x20;   "input\_token\_details": {

&#x20;     "text\_tokens": 0,

&#x20;     "audio\_tokens": 14

&#x20;   },

&#x20;   "output\_tokens": 45,

&#x20;   "total\_tokens": 59

&#x20; }

}

```



\### Word timestamps



```http

curl https://api.openai.com/v1/audio/transcriptions \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: multipart/form-data" \\

&#x20; -F file="@/path/to/file/audio.mp3" \\

&#x20; -F "timestamp\_granularities\[]=word" \\

&#x20; -F model="whisper-1" \\

&#x20; -F response\_format="verbose\_json"

```



\#### Response



```json

{

&#x20; "task": "transcribe",

&#x20; "language": "english",

&#x20; "duration": 8.470000267028809,

&#x20; "text": "The beach was a popular spot on a hot summer day. People were swimming in the ocean, building sandcastles, and playing beach volleyball.",

&#x20; "words": \[

&#x20;   {

&#x20;     "word": "The",

&#x20;     "start": 0.0,

&#x20;     "end": 0.23999999463558197

&#x20;   },

&#x20;   ...

&#x20;   {

&#x20;     "word": "volleyball",

&#x20;     "start": 7.400000095367432,

&#x20;     "end": 7.900000095367432

&#x20;   }

&#x20; ],

&#x20; "usage": {

&#x20;   "type": "duration",

&#x20;   "seconds": 9

&#x20; }

}

```



\### Segment timestamps



```http

curl https://api.openai.com/v1/audio/transcriptions \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: multipart/form-data" \\

&#x20; -F file="@/path/to/file/audio.mp3" \\

&#x20; -F "timestamp\_granularities\[]=segment" \\

&#x20; -F model="whisper-1" \\

&#x20; -F response\_format="verbose\_json"

```



\#### Response



```json

{

&#x20; "task": "transcribe",

&#x20; "language": "english",

&#x20; "duration": 8.470000267028809,

&#x20; "text": "The beach was a popular spot on a hot summer day. People were swimming in the ocean, building sandcastles, and playing beach volleyball.",

&#x20; "segments": \[

&#x20;   {

&#x20;     "id": 0,

&#x20;     "seek": 0,

&#x20;     "start": 0.0,

&#x20;     "end": 3.319999933242798,

&#x20;     "text": " The beach was a popular spot on a hot summer day.",

&#x20;     "tokens": \[

&#x20;       50364, 440, 7534, 390, 257, 3743, 4008, 322, 257, 2368, 4266, 786, 13, 50530

&#x20;     ],

&#x20;     "temperature": 0.0,

&#x20;     "avg\_logprob": -0.2860786020755768,

&#x20;     "compression\_ratio": 1.2363636493682861,

&#x20;     "no\_speech\_prob": 0.00985979475080967

&#x20;   },

&#x20;   ...

&#x20; ],

&#x20; "usage": {

&#x20;   "type": "duration",

&#x20;   "seconds": 9

&#x20; }

}

```



\## Domain Types



\### Transcription



\- `Transcription object { text, logprobs, usage }`



&#x20; Represents a transcription response returned by model, based on the provided input.



&#x20; - `text: string`



&#x20;   The transcribed text.



&#x20; - `logprobs: optional array of object { token, bytes, logprob }`



&#x20;   The log probabilities of the tokens in the transcription. Only returned with the models `gpt-4o-transcribe` and `gpt-4o-mini-transcribe` if `logprobs` is added to the `include` array.



&#x20;   - `token: optional string`



&#x20;     The token in the transcription.



&#x20;   - `bytes: optional array of number`



&#x20;     The bytes of the token.



&#x20;   - `logprob: optional number`



&#x20;     The log probability of the token.



&#x20; - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }  or object { seconds, type }`



&#x20;   Token usage statistics for the request.



&#x20;   - `TokenUsage object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;     Usage statistics for models billed by token usage.



&#x20;     - `input\_tokens: number`



&#x20;       Number of input tokens billed for this request.



&#x20;     - `output\_tokens: number`



&#x20;       Number of output tokens generated.



&#x20;     - `total\_tokens: number`



&#x20;       Total number of tokens used (input + output).



&#x20;     - `type: "tokens"`



&#x20;       The type of the usage object. Always `tokens` for this variant.



&#x20;       - `"tokens"`



&#x20;     - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;       Details about the input tokens billed for this request.



&#x20;       - `audio\_tokens: optional number`



&#x20;         Number of audio tokens billed for this request.



&#x20;       - `text\_tokens: optional number`



&#x20;         Number of text tokens billed for this request.



&#x20;   - `DurationUsage object { seconds, type }`



&#x20;     Usage statistics for models billed by audio input duration.



&#x20;     - `seconds: number`



&#x20;       Duration of the input audio in seconds.



&#x20;     - `type: "duration"`



&#x20;       The type of the usage object. Always `duration` for this variant.



&#x20;       - `"duration"`



\### Transcription Diarized



\- `TranscriptionDiarized object { duration, segments, task, 2 more }`



&#x20; Represents a diarized transcription response returned by the model, including the combined transcript and speaker-segment annotations.



&#x20; - `duration: number`



&#x20;   Duration of the input audio in seconds.



&#x20; - `segments: array of TranscriptionDiarizedSegment`



&#x20;   Segments of the transcript annotated with timestamps and speaker labels.



&#x20;   - `id: string`



&#x20;     Unique identifier for the segment.



&#x20;   - `end: number`



&#x20;     End timestamp of the segment in seconds.



&#x20;   - `speaker: string`



&#x20;     Speaker label for this segment. When known speakers are provided, the label matches `known\_speaker\_names\[]`. Otherwise speakers are labeled sequentially using capital letters (`A`, `B`, ...).



&#x20;   - `start: number`



&#x20;     Start timestamp of the segment in seconds.



&#x20;   - `text: string`



&#x20;     Transcript text for this segment.



&#x20;   - `type: "transcript.text.segment"`



&#x20;     The type of the segment. Always `transcript.text.segment`.



&#x20;     - `"transcript.text.segment"`



&#x20; - `task: "transcribe"`



&#x20;   The type of task that was run. Always `transcribe`.



&#x20;   - `"transcribe"`



&#x20; - `text: string`



&#x20;   The concatenated transcript text for the entire audio input.



&#x20; - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }  or object { seconds, type }`



&#x20;   Token or duration usage statistics for the request.



&#x20;   - `Tokens object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;     Usage statistics for models billed by token usage.



&#x20;     - `input\_tokens: number`



&#x20;       Number of input tokens billed for this request.



&#x20;     - `output\_tokens: number`



&#x20;       Number of output tokens generated.



&#x20;     - `total\_tokens: number`



&#x20;       Total number of tokens used (input + output).



&#x20;     - `type: "tokens"`



&#x20;       The type of the usage object. Always `tokens` for this variant.



&#x20;       - `"tokens"`



&#x20;     - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;       Details about the input tokens billed for this request.



&#x20;       - `audio\_tokens: optional number`



&#x20;         Number of audio tokens billed for this request.



&#x20;       - `text\_tokens: optional number`



&#x20;         Number of text tokens billed for this request.



&#x20;   - `Duration object { seconds, type }`



&#x20;     Usage statistics for models billed by audio input duration.



&#x20;     - `seconds: number`



&#x20;       Duration of the input audio in seconds.



&#x20;     - `type: "duration"`



&#x20;       The type of the usage object. Always `duration` for this variant.



&#x20;       - `"duration"`



\### Transcription Diarized Segment



\- `TranscriptionDiarizedSegment object { id, end, speaker, 3 more }`



&#x20; A segment of diarized transcript text with speaker metadata.



&#x20; - `id: string`



&#x20;   Unique identifier for the segment.



&#x20; - `end: number`



&#x20;   End timestamp of the segment in seconds.



&#x20; - `speaker: string`



&#x20;   Speaker label for this segment. When known speakers are provided, the label matches `known\_speaker\_names\[]`. Otherwise speakers are labeled sequentially using capital letters (`A`, `B`, ...).



&#x20; - `start: number`



&#x20;   Start timestamp of the segment in seconds.



&#x20; - `text: string`



&#x20;   Transcript text for this segment.



&#x20; - `type: "transcript.text.segment"`



&#x20;   The type of the segment. Always `transcript.text.segment`.



&#x20;   - `"transcript.text.segment"`



\### Transcription Include



\- `TranscriptionInclude = "logprobs"`



&#x20; - `"logprobs"`



\### Transcription Segment



\- `TranscriptionSegment object { id, avg\_logprob, compression\_ratio, 7 more }`



&#x20; - `id: number`



&#x20;   Unique identifier of the segment.



&#x20; - `avg\_logprob: number`



&#x20;   Average logprob of the segment. If the value is lower than -1, consider the logprobs failed.



&#x20; - `compression\_ratio: number`



&#x20;   Compression ratio of the segment. If the value is greater than 2.4, consider the compression failed.



&#x20; - `end: number`



&#x20;   End time of the segment in seconds.



&#x20; - `no\_speech\_prob: number`



&#x20;   Probability of no speech in the segment. If the value is higher than 1.0 and the `avg\_logprob` is below -1, consider this segment silent.



&#x20; - `seek: number`



&#x20;   Seek offset of the segment.



&#x20; - `start: number`



&#x20;   Start time of the segment in seconds.



&#x20; - `temperature: number`



&#x20;   Temperature parameter used for generating the segment.



&#x20; - `text: string`



&#x20;   Text content of the segment.



&#x20; - `tokens: array of number`



&#x20;   Array of token IDs for the text content.



\### Transcription Stream Event



\- `TranscriptionStreamEvent = TranscriptionTextSegmentEvent or TranscriptionTextDeltaEvent or TranscriptionTextDoneEvent`



&#x20; Emitted when a diarized transcription returns a completed segment with speaker information. Only emitted when you \[create a transcription](/docs/api-reference/audio/create-transcription) with `stream` set to `true` and `response\_format` set to `diarized\_json`.



&#x20; - `TranscriptionTextSegmentEvent object { id, end, speaker, 3 more }`



&#x20;   Emitted when a diarized transcription returns a completed segment with speaker information. Only emitted when you \[create a transcription](/docs/api-reference/audio/create-transcription) with `stream` set to `true` and `response\_format` set to `diarized\_json`.



&#x20;   - `id: string`



&#x20;     Unique identifier for the segment.



&#x20;   - `end: number`



&#x20;     End timestamp of the segment in seconds.



&#x20;   - `speaker: string`



&#x20;     Speaker label for this segment.



&#x20;   - `start: number`



&#x20;     Start timestamp of the segment in seconds.



&#x20;   - `text: string`



&#x20;     Transcript text for this segment.



&#x20;   - `type: "transcript.text.segment"`



&#x20;     The type of the event. Always `transcript.text.segment`.



&#x20;     - `"transcript.text.segment"`



&#x20; - `TranscriptionTextDeltaEvent object { delta, type, logprobs, segment\_id }`



&#x20;   Emitted when there is an additional text delta. This is also the first event emitted when the transcription starts. Only emitted when you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `Stream` parameter set to `true`.



&#x20;   - `delta: string`



&#x20;     The text delta that was additionally transcribed.



&#x20;   - `type: "transcript.text.delta"`



&#x20;     The type of the event. Always `transcript.text.delta`.



&#x20;     - `"transcript.text.delta"`



&#x20;   - `logprobs: optional array of object { token, bytes, logprob }`



&#x20;     The log probabilities of the delta. Only included if you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `include\[]` parameter set to `logprobs`.



&#x20;     - `token: optional string`



&#x20;       The token that was used to generate the log probability.



&#x20;     - `bytes: optional array of number`



&#x20;       The bytes that were used to generate the log probability.



&#x20;     - `logprob: optional number`



&#x20;       The log probability of the token.



&#x20;   - `segment\_id: optional string`



&#x20;     Identifier of the diarized segment that this delta belongs to. Only present when using `gpt-4o-transcribe-diarize`.



&#x20; - `TranscriptionTextDoneEvent object { text, type, logprobs, usage }`



&#x20;   Emitted when the transcription is complete. Contains the complete transcription text. Only emitted when you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `Stream` parameter set to `true`.



&#x20;   - `text: string`



&#x20;     The text that was transcribed.



&#x20;   - `type: "transcript.text.done"`



&#x20;     The type of the event. Always `transcript.text.done`.



&#x20;     - `"transcript.text.done"`



&#x20;   - `logprobs: optional array of object { token, bytes, logprob }`



&#x20;     The log probabilities of the individual tokens in the transcription. Only included if you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `include\[]` parameter set to `logprobs`.



&#x20;     - `token: optional string`



&#x20;       The token that was used to generate the log probability.



&#x20;     - `bytes: optional array of number`



&#x20;       The bytes that were used to generate the log probability.



&#x20;     - `logprob: optional number`



&#x20;       The log probability of the token.



&#x20;   - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;     Usage statistics for models billed by token usage.



&#x20;     - `input\_tokens: number`



&#x20;       Number of input tokens billed for this request.



&#x20;     - `output\_tokens: number`



&#x20;       Number of output tokens generated.



&#x20;     - `total\_tokens: number`



&#x20;       Total number of tokens used (input + output).



&#x20;     - `type: "tokens"`



&#x20;       The type of the usage object. Always `tokens` for this variant.



&#x20;       - `"tokens"`



&#x20;     - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;       Details about the input tokens billed for this request.



&#x20;       - `audio\_tokens: optional number`



&#x20;         Number of audio tokens billed for this request.



&#x20;       - `text\_tokens: optional number`



&#x20;         Number of text tokens billed for this request.



\### Transcription Text Delta Event



\- `TranscriptionTextDeltaEvent object { delta, type, logprobs, segment\_id }`



&#x20; Emitted when there is an additional text delta. This is also the first event emitted when the transcription starts. Only emitted when you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `Stream` parameter set to `true`.



&#x20; - `delta: string`



&#x20;   The text delta that was additionally transcribed.



&#x20; - `type: "transcript.text.delta"`



&#x20;   The type of the event. Always `transcript.text.delta`.



&#x20;   - `"transcript.text.delta"`



&#x20; - `logprobs: optional array of object { token, bytes, logprob }`



&#x20;   The log probabilities of the delta. Only included if you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `include\[]` parameter set to `logprobs`.



&#x20;   - `token: optional string`



&#x20;     The token that was used to generate the log probability.



&#x20;   - `bytes: optional array of number`



&#x20;     The bytes that were used to generate the log probability.



&#x20;   - `logprob: optional number`



&#x20;     The log probability of the token.



&#x20; - `segment\_id: optional string`



&#x20;   Identifier of the diarized segment that this delta belongs to. Only present when using `gpt-4o-transcribe-diarize`.



\### Transcription Text Done Event



\- `TranscriptionTextDoneEvent object { text, type, logprobs, usage }`



&#x20; Emitted when the transcription is complete. Contains the complete transcription text. Only emitted when you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `Stream` parameter set to `true`.



&#x20; - `text: string`



&#x20;   The text that was transcribed.



&#x20; - `type: "transcript.text.done"`



&#x20;   The type of the event. Always `transcript.text.done`.



&#x20;   - `"transcript.text.done"`



&#x20; - `logprobs: optional array of object { token, bytes, logprob }`



&#x20;   The log probabilities of the individual tokens in the transcription. Only included if you \[create a transcription](/docs/api-reference/audio/create-transcription) with the `include\[]` parameter set to `logprobs`.



&#x20;   - `token: optional string`



&#x20;     The token that was used to generate the log probability.



&#x20;   - `bytes: optional array of number`



&#x20;     The bytes that were used to generate the log probability.



&#x20;   - `logprob: optional number`



&#x20;     The log probability of the token.



&#x20; - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;   Usage statistics for models billed by token usage.



&#x20;   - `input\_tokens: number`



&#x20;     Number of input tokens billed for this request.



&#x20;   - `output\_tokens: number`



&#x20;     Number of output tokens generated.



&#x20;   - `total\_tokens: number`



&#x20;     Total number of tokens used (input + output).



&#x20;   - `type: "tokens"`



&#x20;     The type of the usage object. Always `tokens` for this variant.



&#x20;     - `"tokens"`



&#x20;   - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;     Details about the input tokens billed for this request.



&#x20;     - `audio\_tokens: optional number`



&#x20;       Number of audio tokens billed for this request.



&#x20;     - `text\_tokens: optional number`



&#x20;       Number of text tokens billed for this request.



\### Transcription Text Segment Event



\- `TranscriptionTextSegmentEvent object { id, end, speaker, 3 more }`



&#x20; Emitted when a diarized transcription returns a completed segment with speaker information. Only emitted when you \[create a transcription](/docs/api-reference/audio/create-transcription) with `stream` set to `true` and `response\_format` set to `diarized\_json`.



&#x20; - `id: string`



&#x20;   Unique identifier for the segment.



&#x20; - `end: number`



&#x20;   End timestamp of the segment in seconds.



&#x20; - `speaker: string`



&#x20;   Speaker label for this segment.



&#x20; - `start: number`



&#x20;   Start timestamp of the segment in seconds.



&#x20; - `text: string`



&#x20;   Transcript text for this segment.



&#x20; - `type: "transcript.text.segment"`



&#x20;   The type of the event. Always `transcript.text.segment`.



&#x20;   - `"transcript.text.segment"`



\### Transcription Verbose



\- `TranscriptionVerbose object { duration, language, text, 3 more }`



&#x20; Represents a verbose json transcription response returned by model, based on the provided input.



&#x20; - `duration: number`



&#x20;   The duration of the input audio.



&#x20; - `language: string`



&#x20;   The language of the input audio.



&#x20; - `text: string`



&#x20;   The transcribed text.



&#x20; - `segments: optional array of TranscriptionSegment`



&#x20;   Segments of the transcribed text and their corresponding details.



&#x20;   - `id: number`



&#x20;     Unique identifier of the segment.



&#x20;   - `avg\_logprob: number`



&#x20;     Average logprob of the segment. If the value is lower than -1, consider the logprobs failed.



&#x20;   - `compression\_ratio: number`



&#x20;     Compression ratio of the segment. If the value is greater than 2.4, consider the compression failed.



&#x20;   - `end: number`



&#x20;     End time of the segment in seconds.



&#x20;   - `no\_speech\_prob: number`



&#x20;     Probability of no speech in the segment. If the value is higher than 1.0 and the `avg\_logprob` is below -1, consider this segment silent.



&#x20;   - `seek: number`



&#x20;     Seek offset of the segment.



&#x20;   - `start: number`



&#x20;     Start time of the segment in seconds.



&#x20;   - `temperature: number`



&#x20;     Temperature parameter used for generating the segment.



&#x20;   - `text: string`



&#x20;     Text content of the segment.



&#x20;   - `tokens: array of number`



&#x20;     Array of token IDs for the text content.



&#x20; - `usage: optional object { seconds, type }`



&#x20;   Usage statistics for models billed by audio input duration.



&#x20;   - `seconds: number`



&#x20;     Duration of the input audio in seconds.



&#x20;   - `type: "duration"`



&#x20;     The type of the usage object. Always `duration` for this variant.



&#x20;     - `"duration"`



&#x20; - `words: optional array of TranscriptionWord`



&#x20;   Extracted words and their corresponding timestamps.



&#x20;   - `end: number`



&#x20;     End time of the word in seconds.



&#x20;   - `start: number`



&#x20;     Start time of the word in seconds.



&#x20;   - `word: string`



&#x20;     The text content of the word.



\### Transcription Word



\- `TranscriptionWord object { end, start, word }`



&#x20; - `end: number`



&#x20;   End time of the word in seconds.



&#x20; - `start: number`



&#x20;   Start time of the word in seconds.



&#x20; - `word: string`



&#x20;   The text content of the word.



\### Transcription Create Response



\- `TranscriptionCreateResponse = Transcription or TranscriptionDiarized or TranscriptionVerbose`



&#x20; Represents a transcription response returned by model, based on the provided input.



&#x20; - `Transcription object { text, logprobs, usage }`



&#x20;   Represents a transcription response returned by model, based on the provided input.



&#x20;   - `text: string`



&#x20;     The transcribed text.



&#x20;   - `logprobs: optional array of object { token, bytes, logprob }`



&#x20;     The log probabilities of the tokens in the transcription. Only returned with the models `gpt-4o-transcribe` and `gpt-4o-mini-transcribe` if `logprobs` is added to the `include` array.



&#x20;     - `token: optional string`



&#x20;       The token in the transcription.



&#x20;     - `bytes: optional array of number`



&#x20;       The bytes of the token.



&#x20;     - `logprob: optional number`



&#x20;       The log probability of the token.



&#x20;   - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }  or object { seconds, type }`



&#x20;     Token usage statistics for the request.



&#x20;     - `TokenUsage object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;       Usage statistics for models billed by token usage.



&#x20;       - `input\_tokens: number`



&#x20;         Number of input tokens billed for this request.



&#x20;       - `output\_tokens: number`



&#x20;         Number of output tokens generated.



&#x20;       - `total\_tokens: number`



&#x20;         Total number of tokens used (input + output).



&#x20;       - `type: "tokens"`



&#x20;         The type of the usage object. Always `tokens` for this variant.



&#x20;         - `"tokens"`



&#x20;       - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;         Details about the input tokens billed for this request.



&#x20;         - `audio\_tokens: optional number`



&#x20;           Number of audio tokens billed for this request.



&#x20;         - `text\_tokens: optional number`



&#x20;           Number of text tokens billed for this request.



&#x20;     - `DurationUsage object { seconds, type }`



&#x20;       Usage statistics for models billed by audio input duration.



&#x20;       - `seconds: number`



&#x20;         Duration of the input audio in seconds.



&#x20;       - `type: "duration"`



&#x20;         The type of the usage object. Always `duration` for this variant.



&#x20;         - `"duration"`



&#x20; - `TranscriptionDiarized object { duration, segments, task, 2 more }`



&#x20;   Represents a diarized transcription response returned by the model, including the combined transcript and speaker-segment annotations.



&#x20;   - `duration: number`



&#x20;     Duration of the input audio in seconds.



&#x20;   - `segments: array of TranscriptionDiarizedSegment`



&#x20;     Segments of the transcript annotated with timestamps and speaker labels.



&#x20;     - `id: string`



&#x20;       Unique identifier for the segment.



&#x20;     - `end: number`



&#x20;       End timestamp of the segment in seconds.



&#x20;     - `speaker: string`



&#x20;       Speaker label for this segment. When known speakers are provided, the label matches `known\_speaker\_names\[]`. Otherwise speakers are labeled sequentially using capital letters (`A`, `B`, ...).



&#x20;     - `start: number`



&#x20;       Start timestamp of the segment in seconds.



&#x20;     - `text: string`



&#x20;       Transcript text for this segment.



&#x20;     - `type: "transcript.text.segment"`



&#x20;       The type of the segment. Always `transcript.text.segment`.



&#x20;       - `"transcript.text.segment"`



&#x20;   - `task: "transcribe"`



&#x20;     The type of task that was run. Always `transcribe`.



&#x20;     - `"transcribe"`



&#x20;   - `text: string`



&#x20;     The concatenated transcript text for the entire audio input.



&#x20;   - `usage: optional object { input\_tokens, output\_tokens, total\_tokens, 2 more }  or object { seconds, type }`



&#x20;     Token or duration usage statistics for the request.



&#x20;     - `Tokens object { input\_tokens, output\_tokens, total\_tokens, 2 more }`



&#x20;       Usage statistics for models billed by token usage.



&#x20;       - `input\_tokens: number`



&#x20;         Number of input tokens billed for this request.



&#x20;       - `output\_tokens: number`



&#x20;         Number of output tokens generated.



&#x20;       - `total\_tokens: number`



&#x20;         Total number of tokens used (input + output).



&#x20;       - `type: "tokens"`



&#x20;         The type of the usage object. Always `tokens` for this variant.



&#x20;         - `"tokens"`



&#x20;       - `input\_token\_details: optional object { audio\_tokens, text\_tokens }`



&#x20;         Details about the input tokens billed for this request.



&#x20;         - `audio\_tokens: optional number`



&#x20;           Number of audio tokens billed for this request.



&#x20;         - `text\_tokens: optional number`



&#x20;           Number of text tokens billed for this request.



&#x20;     - `Duration object { seconds, type }`



&#x20;       Usage statistics for models billed by audio input duration.



&#x20;       - `seconds: number`



&#x20;         Duration of the input audio in seconds.



&#x20;       - `type: "duration"`



&#x20;         The type of the usage object. Always `duration` for this variant.



&#x20;         - `"duration"`



&#x20; - `TranscriptionVerbose object { duration, language, text, 3 more }`



&#x20;   Represents a verbose json transcription response returned by model, based on the provided input.



&#x20;   - `duration: number`



&#x20;     The duration of the input audio.



&#x20;   - `language: string`



&#x20;     The language of the input audio.



&#x20;   - `text: string`



&#x20;     The transcribed text.



&#x20;   - `segments: optional array of TranscriptionSegment`



&#x20;     Segments of the transcribed text and their corresponding details.



&#x20;     - `id: number`



&#x20;       Unique identifier of the segment.



&#x20;     - `avg\_logprob: number`



&#x20;       Average logprob of the segment. If the value is lower than -1, consider the logprobs failed.



&#x20;     - `compression\_ratio: number`



&#x20;       Compression ratio of the segment. If the value is greater than 2.4, consider the compression failed.



&#x20;     - `end: number`



&#x20;       End time of the segment in seconds.



&#x20;     - `no\_speech\_prob: number`



&#x20;       Probability of no speech in the segment. If the value is higher than 1.0 and the `avg\_logprob` is below -1, consider this segment silent.



&#x20;     - `seek: number`



&#x20;       Seek offset of the segment.



&#x20;     - `start: number`



&#x20;       Start time of the segment in seconds.



&#x20;     - `temperature: number`



&#x20;       Temperature parameter used for generating the segment.



&#x20;     - `text: string`



&#x20;       Text content of the segment.



&#x20;     - `tokens: array of number`



&#x20;       Array of token IDs for the text content.



&#x20;   - `usage: optional object { seconds, type }`



&#x20;     Usage statistics for models billed by audio input duration.



&#x20;     - `seconds: number`



&#x20;       Duration of the input audio in seconds.



&#x20;     - `type: "duration"`



&#x20;       The type of the usage object. Always `duration` for this variant.



&#x20;       - `"duration"`



&#x20;   - `words: optional array of TranscriptionWord`



&#x20;     Extracted words and their corresponding timestamps.



&#x20;     - `end: number`



&#x20;       End time of the word in seconds.



&#x20;     - `start: number`



&#x20;       Start time of the word in seconds.



&#x20;     - `word: string`



&#x20;       The text content of the word.



\# Translations



\## Create translation



\*\*post\*\* `/audio/translations`



Translates audio into English.



\### Returns



\- `Translation object { text }`



&#x20; - `text: string`



\- `TranslationVerbose object { duration, language, text, segments }`



&#x20; - `duration: number`



&#x20;   The duration of the input audio.



&#x20; - `language: string`



&#x20;   The language of the output translation (always `english`).



&#x20; - `text: string`



&#x20;   The translated text.



&#x20; - `segments: optional array of TranscriptionSegment`



&#x20;   Segments of the translated text and their corresponding details.



&#x20;   - `id: number`



&#x20;     Unique identifier of the segment.



&#x20;   - `avg\_logprob: number`



&#x20;     Average logprob of the segment. If the value is lower than -1, consider the logprobs failed.



&#x20;   - `compression\_ratio: number`



&#x20;     Compression ratio of the segment. If the value is greater than 2.4, consider the compression failed.



&#x20;   - `end: number`



&#x20;     End time of the segment in seconds.



&#x20;   - `no\_speech\_prob: number`



&#x20;     Probability of no speech in the segment. If the value is higher than 1.0 and the `avg\_logprob` is below -1, consider this segment silent.



&#x20;   - `seek: number`



&#x20;     Seek offset of the segment.



&#x20;   - `start: number`



&#x20;     Start time of the segment in seconds.



&#x20;   - `temperature: number`



&#x20;     Temperature parameter used for generating the segment.



&#x20;   - `text: string`



&#x20;     Text content of the segment.



&#x20;   - `tokens: array of number`



&#x20;     Array of token IDs for the text content.



\### Example



```http

curl https://api.openai.com/v1/audio/translations \\

&#x20;   -H 'Content-Type: multipart/form-data' \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20;   -F 'file=@/path/to/file' \\

&#x20;   -F model=whisper-1

```



\#### Response



```json

{

&#x20; "text": "text"

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/translations \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: multipart/form-data" \\

&#x20; -F file="@/path/to/file/german.m4a" \\

&#x20; -F model="whisper-1"

```



\#### Response



```json

{

&#x20; "text": "Hello, my name is Wolfgang and I come from Germany. Where are you heading today?"

}

```



\## Domain Types



\### Translation



\- `Translation object { text }`



&#x20; - `text: string`



\### Translation Verbose



\- `TranslationVerbose object { duration, language, text, segments }`



&#x20; - `duration: number`



&#x20;   The duration of the input audio.



&#x20; - `language: string`



&#x20;   The language of the output translation (always `english`).



&#x20; - `text: string`



&#x20;   The translated text.



&#x20; - `segments: optional array of TranscriptionSegment`



&#x20;   Segments of the translated text and their corresponding details.



&#x20;   - `id: number`



&#x20;     Unique identifier of the segment.



&#x20;   - `avg\_logprob: number`



&#x20;     Average logprob of the segment. If the value is lower than -1, consider the logprobs failed.



&#x20;   - `compression\_ratio: number`



&#x20;     Compression ratio of the segment. If the value is greater than 2.4, consider the compression failed.



&#x20;   - `end: number`



&#x20;     End time of the segment in seconds.



&#x20;   - `no\_speech\_prob: number`



&#x20;     Probability of no speech in the segment. If the value is higher than 1.0 and the `avg\_logprob` is below -1, consider this segment silent.



&#x20;   - `seek: number`



&#x20;     Seek offset of the segment.



&#x20;   - `start: number`



&#x20;     Start time of the segment in seconds.



&#x20;   - `temperature: number`



&#x20;     Temperature parameter used for generating the segment.



&#x20;   - `text: string`



&#x20;     Text content of the segment.



&#x20;   - `tokens: array of number`



&#x20;     Array of token IDs for the text content.



\### Translation Create Response



\- `TranslationCreateResponse = Translation or TranslationVerbose`



&#x20; - `Translation object { text }`



&#x20;   - `text: string`



&#x20; - `TranslationVerbose object { duration, language, text, segments }`



&#x20;   - `duration: number`



&#x20;     The duration of the input audio.



&#x20;   - `language: string`



&#x20;     The language of the output translation (always `english`).



&#x20;   - `text: string`



&#x20;     The translated text.



&#x20;   - `segments: optional array of TranscriptionSegment`



&#x20;     Segments of the translated text and their corresponding details.



&#x20;     - `id: number`



&#x20;       Unique identifier of the segment.



&#x20;     - `avg\_logprob: number`



&#x20;       Average logprob of the segment. If the value is lower than -1, consider the logprobs failed.



&#x20;     - `compression\_ratio: number`



&#x20;       Compression ratio of the segment. If the value is greater than 2.4, consider the compression failed.



&#x20;     - `end: number`



&#x20;       End time of the segment in seconds.



&#x20;     - `no\_speech\_prob: number`



&#x20;       Probability of no speech in the segment. If the value is higher than 1.0 and the `avg\_logprob` is below -1, consider this segment silent.



&#x20;     - `seek: number`



&#x20;       Seek offset of the segment.



&#x20;     - `start: number`



&#x20;       Start time of the segment in seconds.



&#x20;     - `temperature: number`



&#x20;       Temperature parameter used for generating the segment.



&#x20;     - `text: string`



&#x20;       Text content of the segment.



&#x20;     - `tokens: array of number`



&#x20;       Array of token IDs for the text content.



\# Speech



\## Create speech



\*\*post\*\* `/audio/speech`



Generates audio from the input text.



Returns the audio file content, or a stream of audio events.



\### Body Parameters



\- `input: string`



&#x20; The text to generate audio for. The maximum length is 4096 characters.



\- `model: string or SpeechModel`



&#x20; One of the available \[TTS models](/docs/models#tts): `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`, or `gpt-4o-mini-tts-2025-12-15`.



&#x20; - `string`



&#x20; - `SpeechModel = "tts-1" or "tts-1-hd" or "gpt-4o-mini-tts" or "gpt-4o-mini-tts-2025-12-15"`



&#x20;   - `"tts-1"`



&#x20;   - `"tts-1-hd"`



&#x20;   - `"gpt-4o-mini-tts"`



&#x20;   - `"gpt-4o-mini-tts-2025-12-15"`



\- `voice: string or "alloy" or "ash" or "ballad" or 7 more or object { id }`



&#x20; The voice to use when generating the audio. Supported built-in voices are `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `onyx`, `nova`, `sage`, `shimmer`, `verse`, `marin`, and `cedar`. You may also provide a custom voice object with an `id`, for example `{ "id": "voice\_1234" }`. Previews of the voices are available in the \[Text to speech guide](/docs/guides/text-to-speech#voice-options).



&#x20; - `string`



&#x20; - `"alloy" or "ash" or "ballad" or 7 more`



&#x20;   - `"alloy"`



&#x20;   - `"ash"`



&#x20;   - `"ballad"`



&#x20;   - `"coral"`



&#x20;   - `"echo"`



&#x20;   - `"sage"`



&#x20;   - `"shimmer"`



&#x20;   - `"verse"`



&#x20;   - `"marin"`



&#x20;   - `"cedar"`



&#x20; - `ID object { id }`



&#x20;   Custom voice reference.



&#x20;   - `id: string`



&#x20;     The custom voice ID, e.g. `voice\_1234`.



\- `instructions: optional string`



&#x20; Control the voice of your generated audio with additional instructions. Does not work with `tts-1` or `tts-1-hd`.



\- `response\_format: optional "mp3" or "opus" or "aac" or 3 more`



&#x20; The format to audio in. Supported formats are `mp3`, `opus`, `aac`, `flac`, `wav`, and `pcm`.



&#x20; - `"mp3"`



&#x20; - `"opus"`



&#x20; - `"aac"`



&#x20; - `"flac"`



&#x20; - `"wav"`



&#x20; - `"pcm"`



\- `speed: optional number`



&#x20; The speed of the generated audio. Select a value from `0.25` to `4.0`. `1.0` is the default.



\- `stream\_format: optional "sse" or "audio"`



&#x20; The format to stream the audio in. Supported formats are `sse` and `audio`. `sse` is not supported for `tts-1` or `tts-1-hd`.



&#x20; - `"sse"`



&#x20; - `"audio"`



\### Example



```http

curl https://api.openai.com/v1/audio/speech \\

&#x20;   -H 'Content-Type: application/json' \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20;   -d '{

&#x20;         "input": "input",

&#x20;         "model": "string",

&#x20;         "voice": "string"

&#x20;       }'

```



\### Example



```http

curl https://api.openai.com/v1/audio/speech \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: application/json" \\

&#x20; -d '{

&#x20;   "model": "gpt-4o-mini-tts",

&#x20;   "input": "The quick brown fox jumped over the lazy dog.",

&#x20;   "voice": "alloy"

&#x20; }' \\

&#x20; --output speech.mp3

```



\### SSE Stream Format



```http

curl https://api.openai.com/v1/audio/speech \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: application/json" \\

&#x20; -d '{

&#x20;   "model": "gpt-4o-mini-tts",

&#x20;   "input": "The quick brown fox jumped over the lazy dog.",

&#x20;   "voice": "alloy",

&#x20;   "stream\_format": "sse"

&#x20; }'

```



\## Domain Types



\### Speech Model



\- `SpeechModel = "tts-1" or "tts-1-hd" or "gpt-4o-mini-tts" or "gpt-4o-mini-tts-2025-12-15"`



&#x20; - `"tts-1"`



&#x20; - `"tts-1-hd"`



&#x20; - `"gpt-4o-mini-tts"`



&#x20; - `"gpt-4o-mini-tts-2025-12-15"`



\# Voices



\## Create voice



\*\*post\*\* `/audio/voices`



Creates a custom voice.



\### Returns



\- `id: string`



&#x20; The voice identifier, which can be referenced in API endpoints.



\- `created\_at: number`



&#x20; The Unix timestamp (in seconds) for when the voice was created.



\- `name: string`



&#x20; The name of the voice.



\- `object: "audio.voice"`



&#x20; The object type, which is always `audio.voice`.



&#x20; - `"audio.voice"`



\### Example



```http

curl https://api.openai.com/v1/audio/voices \\

&#x20;   -H 'Content-Type: multipart/form-data' \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20;   -F 'audio\_sample=@/path/to/audio\_sample' \\

&#x20;   -F consent=consent \\

&#x20;   -F name=name

```



\#### Response



```json

{

&#x20; "id": "id",

&#x20; "created\_at": 0,

&#x20; "name": "name",

&#x20; "object": "audio.voice"

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/voices \\

&#x20; -X POST \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -F "name=My new voice" \\

&#x20; -F "consent=cons\_1234" \\

&#x20; -F "audio\_sample=@$HOME/audio\_sample.wav;type=audio/x-wav"

```



\## Domain Types



\### Voice Create Response



\- `VoiceCreateResponse object { id, created\_at, name, object }`



&#x20; A custom voice that can be used for audio output.



&#x20; - `id: string`



&#x20;   The voice identifier, which can be referenced in API endpoints.



&#x20; - `created\_at: number`



&#x20;   The Unix timestamp (in seconds) for when the voice was created.



&#x20; - `name: string`



&#x20;   The name of the voice.



&#x20; - `object: "audio.voice"`



&#x20;   The object type, which is always `audio.voice`.



&#x20;   - `"audio.voice"`



\# Voice Consents



\## List voice consents



\*\*get\*\* `/audio/voice\_consents`



Returns a list of voice consent recordings.



\### Query Parameters



\- `after: optional string`



&#x20; A cursor for use in pagination. `after` is an object ID that defines your place in the list. For instance, if you make a list request and receive 100 objects, ending with obj\_foo, your subsequent call can include after=obj\_foo in order to fetch the next page of the list.



\- `limit: optional number`



&#x20; A limit on the number of objects to be returned. Limit can range between 1 and 100, and the default is 20.



\### Returns



\- `data: array of object { id, created\_at, language, 2 more }`



&#x20; - `id: string`



&#x20;   The consent recording identifier.



&#x20; - `created\_at: number`



&#x20;   The Unix timestamp (in seconds) for when the consent recording was created.



&#x20; - `language: string`



&#x20;   The BCP 47 language tag for the consent phrase (for example, `en-US`).



&#x20; - `name: string`



&#x20;   The label provided when the consent recording was uploaded.



&#x20; - `object: "audio.voice\_consent"`



&#x20;   The object type, which is always `audio.voice\_consent`.



&#x20;   - `"audio.voice\_consent"`



\- `has\_more: boolean`



\- `object: "list"`



&#x20; - `"list"`



\- `first\_id: optional string`



\- `last\_id: optional string`



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY"

```



\#### Response



```json

{

&#x20; "data": \[

&#x20;   {

&#x20;     "id": "cons\_1234",

&#x20;     "created\_at": 0,

&#x20;     "language": "language",

&#x20;     "name": "name",

&#x20;     "object": "audio.voice\_consent"

&#x20;   }

&#x20; ],

&#x20; "has\_more": true,

&#x20; "object": "list",

&#x20; "first\_id": "first\_id",

&#x20; "last\_id": "last\_id"

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents?limit=20 \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY"

```



\## Create voice consent



\*\*post\*\* `/audio/voice\_consents`



Upload a voice consent recording.



\### Returns



\- `id: string`



&#x20; The consent recording identifier.



\- `created\_at: number`



&#x20; The Unix timestamp (in seconds) for when the consent recording was created.



\- `language: string`



&#x20; The BCP 47 language tag for the consent phrase (for example, `en-US`).



\- `name: string`



&#x20; The label provided when the consent recording was uploaded.



\- `object: "audio.voice\_consent"`



&#x20; The object type, which is always `audio.voice\_consent`.



&#x20; - `"audio.voice\_consent"`



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents \\

&#x20;   -H 'Content-Type: multipart/form-data' \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20;   -F language=language \\

&#x20;   -F name=name \\

&#x20;   -F 'recording=@/path/to/recording'

```



\#### Response



```json

{

&#x20; "id": "cons\_1234",

&#x20; "created\_at": 0,

&#x20; "language": "language",

&#x20; "name": "name",

&#x20; "object": "audio.voice\_consent"

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents \\

&#x20; -X POST \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -F "name=John Doe" \\

&#x20; -F "language=en-US" \\

&#x20; -F "recording=@$HOME/consent\_recording.wav;type=audio/x-wav"

```



\## Retrieve voice consent



\*\*get\*\* `/audio/voice\_consents/{consent\_id}`



Retrieves a voice consent recording.



\### Path Parameters



\- `consent\_id: string`



\### Returns



\- `id: string`



&#x20; The consent recording identifier.



\- `created\_at: number`



&#x20; The Unix timestamp (in seconds) for when the consent recording was created.



\- `language: string`



&#x20; The BCP 47 language tag for the consent phrase (for example, `en-US`).



\- `name: string`



&#x20; The label provided when the consent recording was uploaded.



\- `object: "audio.voice\_consent"`



&#x20; The object type, which is always `audio.voice\_consent`.



&#x20; - `"audio.voice\_consent"`



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents/$CONSENT\_ID \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY"

```



\#### Response



```json

{

&#x20; "id": "cons\_1234",

&#x20; "created\_at": 0,

&#x20; "language": "language",

&#x20; "name": "name",

&#x20; "object": "audio.voice\_consent"

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents/cons\_1234 \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY"

```



\## Update voice consent



\*\*post\*\* `/audio/voice\_consents/{consent\_id}`



Updates a voice consent recording (metadata only).



\### Path Parameters



\- `consent\_id: string`



\### Body Parameters



\- `name: string`



&#x20; The updated label for this consent recording.



\### Returns



\- `id: string`



&#x20; The consent recording identifier.



\- `created\_at: number`



&#x20; The Unix timestamp (in seconds) for when the consent recording was created.



\- `language: string`



&#x20; The BCP 47 language tag for the consent phrase (for example, `en-US`).



\- `name: string`



&#x20; The label provided when the consent recording was uploaded.



\- `object: "audio.voice\_consent"`



&#x20; The object type, which is always `audio.voice\_consent`.



&#x20; - `"audio.voice\_consent"`



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents/$CONSENT\_ID \\

&#x20;   -H 'Content-Type: application/json' \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20;   -d '{

&#x20;         "name": "name"

&#x20;       }'

```



\#### Response



```json

{

&#x20; "id": "cons\_1234",

&#x20; "created\_at": 0,

&#x20; "language": "language",

&#x20; "name": "name",

&#x20; "object": "audio.voice\_consent"

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents/cons\_1234 \\

&#x20; -X POST \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY" \\

&#x20; -H "Content-Type: application/json" \\

&#x20; -d '{

&#x20;   "name": "John Doe"

&#x20; }'

```



\## Delete voice consent



\*\*delete\*\* `/audio/voice\_consents/{consent\_id}`



Deletes a voice consent recording.



\### Path Parameters



\- `consent\_id: string`



\### Returns



\- `id: string`



&#x20; The consent recording identifier.



\- `deleted: boolean`



\- `object: "audio.voice\_consent"`



&#x20; - `"audio.voice\_consent"`



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents/$CONSENT\_ID \\

&#x20;   -X DELETE \\

&#x20;   -H "Authorization: Bearer $OPENAI\_API\_KEY"

```



\#### Response



```json

{

&#x20; "id": "cons\_1234",

&#x20; "deleted": true,

&#x20; "object": "audio.voice\_consent"

}

```



\### Example



```http

curl https://api.openai.com/v1/audio/voice\_consents/cons\_1234 \\

&#x20; -X DELETE \\

&#x20; -H "Authorization: Bearer $OPENAI\_API\_KEY"

```



\## Domain Types



\### Voice Consent List Response



\- `VoiceConsentListResponse object { id, created\_at, language, 2 more }`



&#x20; A consent recording used to authorize creation of a custom voice.



&#x20; - `id: string`



&#x20;   The consent recording identifier.



&#x20; - `created\_at: number`



&#x20;   The Unix timestamp (in seconds) for when the consent recording was created.



&#x20; - `language: string`



&#x20;   The BCP 47 language tag for the consent phrase (for example, `en-US`).



&#x20; - `name: string`



&#x20;   The label provided when the consent recording was uploaded.



&#x20; - `object: "audio.voice\_consent"`



&#x20;   The object type, which is always `audio.voice\_consent`.



&#x20;   - `"audio.voice\_consent"`



\### Voice Consent Create Response



\- `VoiceConsentCreateResponse object { id, created\_at, language, 2 more }`



&#x20; A consent recording used to authorize creation of a custom voice.



&#x20; - `id: string`



&#x20;   The consent recording identifier.



&#x20; - `created\_at: number`



&#x20;   The Unix timestamp (in seconds) for when the consent recording was created.



&#x20; - `language: string`



&#x20;   The BCP 47 language tag for the consent phrase (for example, `en-US`).



&#x20; - `name: string`



&#x20;   The label provided when the consent recording was uploaded.



&#x20; - `object: "audio.voice\_consent"`



&#x20;   The object type, which is always `audio.voice\_consent`.



&#x20;   - `"audio.voice\_consent"`



\### Voice Consent Retrieve Response



\- `VoiceConsentRetrieveResponse object { id, created\_at, language, 2 more }`



&#x20; A consent recording used to authorize creation of a custom voice.



&#x20; - `id: string`



&#x20;   The consent recording identifier.



&#x20; - `created\_at: number`



&#x20;   The Unix timestamp (in seconds) for when the consent recording was created.



&#x20; - `language: string`



&#x20;   The BCP 47 language tag for the consent phrase (for example, `en-US`).



&#x20; - `name: string`



&#x20;   The label provided when the consent recording was uploaded.



&#x20; - `object: "audio.voice\_consent"`



&#x20;   The object type, which is always `audio.voice\_consent`.



&#x20;   - `"audio.voice\_consent"`



\### Voice Consent Update Response



\- `VoiceConsentUpdateResponse object { id, created\_at, language, 2 more }`



&#x20; A consent recording used to authorize creation of a custom voice.



&#x20; - `id: string`



&#x20;   The consent recording identifier.



&#x20; - `created\_at: number`



&#x20;   The Unix timestamp (in seconds) for when the consent recording was created.



&#x20; - `language: string`



&#x20;   The BCP 47 language tag for the consent phrase (for example, `en-US`).



&#x20; - `name: string`



&#x20;   The label provided when the consent recording was uploaded.



&#x20; - `object: "audio.voice\_consent"`



&#x20;   The object type, which is always `audio.voice\_consent`.



&#x20;   - `"audio.voice\_consent"`



\### Voice Consent Delete Response



\- `VoiceConsentDeleteResponse object { id, deleted, object }`



&#x20; - `id: string`



&#x20;   The consent recording identifier.



&#x20; - `deleted: boolean`



&#x20; - `object: "audio.voice\_consent"`



&#x20;   - `"audio.voice\_consent"`



