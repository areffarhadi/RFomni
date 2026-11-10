"""Default pages: ready-made pages for Task sharing, Subjective tests and Data collection, adapted in a chat.

Claude designed one default page per likely task (templates/*.json): per area (tasks, listen, collect), per modality
(voice, text, image, video) and per kind of task (classification, rating, transcription, ...). Each default is a
family of page (settings.py) plus its own wording, layout and behaviour.

Someone making a page picks a modality and describes the task. The small model (Qwen3.5-9B on the 4090, agent.py)
picks the closest default and then only changes its settings, a flat list of named values such as the classes,
the question, the scale or the charts. It never writes page code, so it cannot break a page. The server checks
every value and turns the settings into the page (settings.compile_*). Each change is a version that can be undone;
the owner approves the version they want. What no setting can do goes to the administrators as a request
(requests.py), answered within 12 hours.
"""
