-- imessage_agent.applescript

property lastHandledID : ""

on time_of_day_greeting()
	set h to hours of (current date)
	if h ≥ 5 and h < 12 then
		return "Good morning"
	else if h ≥ 12 and h < 17 then
		return "Good afternoon"
	else if h ≥ 17 and h < 23 then
		return "Good evening"
	else
		return "God it's late"
	end if
end time_of_day_greeting

on ordinal_suffix(n)
	set nMod100 to (n mod 100)
	if nMod100 = 11 or nMod100 = 12 or nMod100 = 13 then return "th"
	
	set nMod10 to (n mod 10)
	if nMod10 = 1 then
		return "st"
	else if nMod10 = 2 then
		return "nd"
	else if nMod10 = 3 then
		return "rd"
	else
		return "th"
	end if
end ordinal_suffix

on pretty_date_string(d)
	set monthName to (month of d as string)
	set dayNum to (day of d as integer)
	set suffix to my ordinal_suffix(dayNum)
	return monthName & " " & (dayNum as string) & suffix
end pretty_date_string

on first_name_from(fullName)
	set AppleScript's text item delimiters to " "
	set fn to first text item of fullName
	set AppleScript's text item delimiters to ""
	return fn
end first_name_from

on poll_new_message()
	tell application "Messages"
		set targetService to first account whose service type is iMessage
		
		set allChats to chats of targetService
		if (count of allChats) = 0 then return missing value
		
		set newestMsg to missing value
		set newestChat to missing value
		set newestDate to date "Monday, January 1, 1900 at 12:00:00 AM"
		
		repeat with c in allChats
			set msgs to messages of c
			if (count of msgs) > 0 then
				set m to last item of msgs
				try
					set d to (date sent of m)
				on error
					set d to (current date)
				end try
				
				if d > newestDate then
					set newestDate to d
					set newestMsg to m
					set newestChat to c
				end if
			end if
		end repeat
		
		if newestMsg is missing value then return missing value
		
		set lastMsg to newestMsg
		set c to newestChat
		
		set msgID to id of lastMsg
		if msgID = lastHandledID then return missing value
		set lastHandledID to msgID
		
		set theBuddy to sender of lastMsg
		set fullName to ""
		try
			set fullName to name of theBuddy
		end try
		if fullName is missing value or fullName = "" then
			try
				set fullName to handle of theBuddy
			end try
		end if
		
		set chatID to id of c
	end tell
	
	set firstName to my first_name_from(fullName)
	set greetingText to my time_of_day_greeting()
	set prettyDate to my pretty_date_string(current date)
	
	return {firstName:firstName, greeting:greetingText, dateStr:prettyDate, chatID:(chatID as string), msgID:(msgID as string)}
end poll_new_message

on send_reply(chatID, replyText)
	tell application "Messages"
		set targetService to first account whose service type is iMessage
		
		set theChat to missing value
		repeat with c in chats of targetService
			if (id of c as string) = (chatID as string) then
				set theChat to c
				exit repeat
			end if
		end repeat
		
		if theChat is not missing value then
			send replyText to theChat
		end if
	end tell
end send_reply
