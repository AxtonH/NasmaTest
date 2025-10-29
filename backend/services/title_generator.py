"""
Title Generator Service
Generates descriptive titles for conversations based on user messages and intents.
"""
import re
from datetime import datetime
from typing import Optional


def generate_conversation_title(user_message: str, max_length: int = 50) -> str:
    """
    Generate a descriptive conversation title from the user's first message.

    Args:
        user_message: The user's first message in the conversation
        max_length: Maximum length for the title

    Returns:
        A descriptive title string
    """
    if not user_message or not user_message.strip():
        return "New Conversation"

    message = user_message.strip()

    # Common request patterns to extract intent
    patterns = {
        # Time off requests
        r'(?:request|apply|submit|need|want|take).*?(?:time off|leave|vacation|holiday|pto|day off)': 'Time Off Request',
        r'(?:sick|medical|emergency).*?leave': 'Sick Leave Request',
        r'(?:annual|vacation).*?leave': 'Annual Leave Request',

        # Half day requests
        r'half[\s-]?day': 'Half Day Request',

        # Overtime requests
        r'(?:overtime|ot|extra hours|work.*?hours)': 'Overtime Request',

        # Reimbursement
        r'(?:reimburs|reimburse|expense|receipt|refund)': 'Reimbursement Request',

        # Documents
        r'(?:employment|experience|embassy).*?(?:letter|certificate|document)': 'Document Request',
        r'(?:letter|certificate|document).*?(?:employment|experience|embassy)': 'Document Request',

        # Salary/payroll
        r'(?:salary|pay|payroll|payment)': 'Salary Inquiry',

        # Benefits
        r'(?:benefit|insurance|medical|health)': 'Benefits Inquiry',

        # General questions
        r'(?:how do i|how can i|how to|what is|what are|when is|when are|where is|where are)': None,  # Extract the actual question
        r'(?:tell me about|info|information about|explain)': None,  # Extract the topic
    }

    # Check for known patterns
    message_lower = message.lower()
    for pattern, title_template in patterns.items():
        if re.search(pattern, message_lower):
            if title_template:
                # Add date to the title
                date_str = datetime.now().strftime('%b %d')
                return f"{title_template} - {date_str}"
            break

    # If no pattern matched, extract key information from the message
    # Remove common filler words
    words_to_remove = [
        'please', 'could', 'would', 'can', 'you', 'i', 'me', 'my',
        'the', 'a', 'an', 'is', 'are', 'am', 'was', 'were', 'be', 'been',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
        'could', 'might', 'may', 'must', 'shall'
    ]

    # Try to extract the main subject
    words = message.split()
    filtered_words = []
    for word in words[:15]:  # Only look at first 15 words
        clean_word = re.sub(r'[^\w\s-]', '', word).strip()
        if clean_word.lower() not in words_to_remove and len(clean_word) > 2:
            filtered_words.append(clean_word)
        if len(' '.join(filtered_words)) >= max_length - 10:  # Leave room for date
            break

    if filtered_words:
        title_base = ' '.join(filtered_words)
        # Capitalize first letter
        title_base = title_base[0].upper() + title_base[1:] if len(title_base) > 0 else title_base
    else:
        # Fallback: use first few words of the original message
        title_base = ' '.join(message.split()[:5])

    # Truncate if too long
    if len(title_base) > max_length - 10:
        title_base = title_base[:max_length - 13] + '...'

    # Add date
    date_str = datetime.now().strftime('%b %d')
    final_title = f"{title_base} - {date_str}"

    # Final length check
    if len(final_title) > max_length:
        final_title = final_title[:max_length - 3] + '...'

    return final_title


def update_title_if_needed(current_title: Optional[str], user_message: str) -> str:
    """
    Update the conversation title only if the current one is not descriptive.

    Args:
        current_title: The current title (may be None or a truncated preview)
        user_message: The user's message to generate title from

    Returns:
        Updated title or the current title if it's already good
    """
    if not current_title or current_title == 'New Conversation':
        return generate_conversation_title(user_message)

    # If current title is just a truncated message (ends with ...), regenerate
    if current_title.endswith('...'):
        return generate_conversation_title(user_message)

    # If current title is too short (likely just "yes", "ok", etc.), regenerate
    if len(current_title.strip()) < 10:
        return generate_conversation_title(user_message)

    return current_title
