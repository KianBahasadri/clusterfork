# Cadmus writing analytics and Hex typing replay

November 25, 2026

Cadmus likely infers transcription from how a draft develops. Its dashboard distinguishes pasting from behaviour consistent with typing out existing text. The exact rule and its accuracy on simulated typing remain unverified. [Cadmus learning assurance guide](https://support.cadmus.io/intro-to-learning-assurance)

## What instructors can review

- **Direct pastes.** Cadmus reports how much text a student pasted and the most common paste source. An individual activity report lets an instructor inspect pasted content alongside editing behaviour and work sessions. [Learning assurance guide](https://support.cadmus.io/intro-to-learning-assurance) · [Activity reports](https://support.cadmus.io/teachers/learning-analytics-reports)
- **Edit history.** Cadmus discloses that it regularly saves the work, records save and submission timestamps, and collects interface activity such as opening assessment resources. Those records give context for how a draft changed. The disclosure leaves open which events feed the transcription indicator. [Cadmus Australian privacy policy](https://support.cadmus.io/privacy-policy-au)
- **Turnitin Similarity.** Instructors can open the report behind the submission’s similarity score to review text matches. Cadmus combines similarity with evidence about the writing process in its overall learning assurance indicator. A low similarity score alone cannot establish how the text entered the editor. [Learning assurance guide](https://support.cadmus.io/intro-to-learning-assurance)

## Evidence for the transcription inference

A 2019 study compared three tasks in Cadmus. Participants composed an essay, transcribed an unfamiliar essay, then transcribed their own. The researchers measured words added and deleted, pauses, and bursts of keystrokes. Participants typically entered more keystrokes per burst when transcribing their own essay than when copying unfamiliar prose, and the fewest when composing. The experiment studied people performing assigned tasks. It provides a basis for interpreting draft development, while leaving the current product rule and its performance on replay an open question. [Original study in the Journal of Learning Analytics](https://research.monash.edu/files/443560003/443559696_oa.pdf)

## Comparisons and the local benchmark

Turnitin Clarity provides a writing timeline and records whether externally pasted passages were later edited. GPTZero’s Writing Report shows edits, sessions and pastes and includes a typing-pattern analysis. Each vendor describes its own writing environment. [Turnitin Clarity student guide](https://guides.turnitin.com/hc/en-us/articles/36982699236877-Getting-Started-with-Turnitin-Clarity-for-Students) · [GPTZero Authorship](https://gptzero.me/authorship)

Hex’s offline benchmark replays the same draft with a fixed seed and two synthetic training profiles. It measures the resulting timing and reports how two local detectors score each trace. Both profiles produce the same final text. Neither fixture yields a Cadmus activity report or transcription classification. [Hex keystroke benchmark](../hex.md#keystroke-benchmark)
