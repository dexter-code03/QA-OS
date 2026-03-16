package Object Repository.AddressScreen


import com.kms.katalon.core.testobject.ObjectRepository as ObjectRepository
import com.kms.katalon.core.testobject.MobileTestObject as MobileTestObject

public class text_AddressSuggestionItem {
    public static MobileTestObject getTestObject() {
        MobileTestObject to = ObjectRepository.findTestObject('Object Repository/AddressScreen/text_AddressSuggestionItem')
        // Using XPath to find a suggestion item by its text content
        // This assumes suggestion items are text elements within a list,
        // and their text content is directly verifiable.
        // The 'suggestionText' variable will be passed from the test case.
        to.setSelectorMethod(MobileTestObject.SELECTOR_METHOD.XPATH)
        to.setSelectorValue("//*[@resource-id='addressSuggestionItem' or @content-desc='addressSuggestionItem' or @text='${suggestionText}']")
        return to
    }
}