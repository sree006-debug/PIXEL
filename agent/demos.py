"""Named demo tasks for manual runs:  python main.py --demo calculator

Each demo is only a task sentence plus how FINISH is checked. Nothing here
knows where anything is on screen: the VLM still finds every UI element.

    expect_screen  extra criterion for the final VISUAL check (GUI-only tasks)
    expect_file    deterministic file check (authoritative for file tasks);
                   the file must be saved during the run
    expect_text / expect_lines   what that file must contain
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Demo:
    task: str
    expect_screen: str = ""
    expect_file: str = ""
    expect_text: str = ""
    expect_lines: int = 0


DEMOS = {
    "calculator": Demo(
        task="Open Calculator and calculate 25 multiplied by 16.",
        expect_screen="the Windows Calculator app is open and its result display shows 400",
    ),
    "chrome": Demo(
        task="Open Chrome and search for ROS2 Humble.",
        expect_screen="a Google Chrome window showing a page of search results for "
                      "ROS2 Humble (the search terms or results mention ROS 2 / ROS2 Humble)",
    ),
    "notepad": Demo(
        task="Open Windows Notepad, write a short list of three robotics technologies "
             "(one per line), and save it as robotics_list.txt on the Desktop.",
        expect_screen="Windows Notepad showing three robotics technologies, one per line, "
                      "in a tab or title named robotics_list.txt",
        expect_file=r"%USERPROFILE%\Desktop\robotics_list.txt",
        expect_lines=3,
    ),
}
