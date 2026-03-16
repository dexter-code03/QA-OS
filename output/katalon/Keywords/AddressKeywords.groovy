package keywords

import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject
import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile
import com.kms.katalon.core.model.FailureHandling
import com.kms.katalon.core.util.KeywordUtil

/**
 * Custom keywords for Address Verification feature in a mobile application.
 * Provides reusable methods for common interactions and validations.
 */
public class AddressKeywords {

    /**
     * Navigates to the address input screen.
     * Assumes the app is already launched and initial onboarding steps (OTP, Name, DOB) are completed.
     * This method should simulate tapping through any preceding screens to reach the address entry.
     */
    @com.kms.katalon.core.annotation.Keyword
    def navigateToAddressScreen() {
        KeywordUtil.logInfo("Navigating to Address Entry Screen...")
        // --- Placeholder for navigation steps ---
        // Example: Tap 'Next' on previous screens until address screen is visible.
        // Mobile.tap(findTestObject('Object Repository/Common/btn_Next'), 10)
        // Mobile.tap(findTestObject('Object Repository/Common/btn_Skip'), 10)
        
        // For simplicity, we assume the address search input is directly visible after setup.
        // In a real scenario, you'd add actual navigation steps here.
        Mobile.waitForElementPresent(findTestObject('Object Repository/AddressScreen/input_AddressSearch'), 30, FailureHandling.STOP_ON_FAILURE)
        KeywordUtil.logInfo("Successfully navigated to Address Entry Screen.")
    }

    /**
     * Enters text into the address search input field.
     * @param address The address string to type.
     */
    @com.kms.katalon.core.annotation.Keyword
    def enterAddressSearch(String address) {
        KeywordUtil.logInfo("Entering address search query: '${address}'")
        Mobile.setText(findTestObject('Object Repository/AddressScreen/input_AddressSearch'), address, 10)
        Mobile.hideKeyboard() // Hide keyboard to ensure suggestions are visible
        Mobile.delay(2) // Allow time for debounce and API call
    }

    /**
     * Selects an address suggestion from the dropdown list.
     * @param suggestionText The full text of the suggestion to select.
     */
    @com.kms.katalon.core.annotation.Keyword
    def selectAddressSuggestion(String suggestionText) {
        KeywordUtil.logInfo("Attempting to select address suggestion: '${suggestionText}'")
        def suggestionObject = findTestObject('Object Repository/AddressScreen/text_AddressSuggestionItem', [('suggestionText') : suggestionText])
        
        Mobile.waitForElementPresent(suggestionObject, 20, FailureHandling.OPTIONAL)
        if (Mobile.verifyElementExist(suggestionObject, 5, FailureHandling.OPTIONAL)) {
            Mobile.tap(suggestionObject, 10)
            KeywordUtil.logInfo("Selected suggestion: '${suggestionText}'")
            Mobile.delay(1) // Wait for mapping to occur
        } else {
            KeywordUtil.markFailed("Suggestion '${suggestionText}' not found or not visible.")
        }
    }
    
    /**
     * Verifies if an address suggestion is present in the list.
     * @param suggestionText The full text of the suggestion to verify.
     * @return true if the suggestion is found, false otherwise.
     */
    @com.kms.katalon.core.annotation.Keyword
    def verifyAddressSuggestionPresent(String suggestionText) {
        KeywordUtil.logInfo("Verifying presence of suggestion: '${suggestionText}'")
        def suggestionObject = findTestObject('Object Repository/AddressScreen/text_AddressSuggestionItem', [('suggestionText') : suggestionText])
        boolean isPresent = Mobile.verifyElementExist(suggestionObject, 10, FailureHandling.OPTIONAL)
        if (isPresent) {
            KeywordUtil.logInfo("Suggestion '${suggestionText}' is present.")
        } else {
            KeywordUtil.logInfo("Suggestion '${suggestionText}' is NOT present.")
        }
        return isPresent
    }

    /**
     * Verifies the mapped individual address fields against expected values.
     * @param expectedFields A map where keys are UI field names (e.g., "Street Name", "City") and values are expected texts.
     */
    @com.kms.katalon.core.annotation.Keyword
    def verifyAddressFields(Map<String, String> expectedFields) {
        KeywordUtil.logInfo("Verifying mapped address fields...")
        def fieldMappings = [
            "Street Name": findTestObject('Object Repository/AddressScreen/input_StreetName'),
            "House Number": findTestObject('Object Repository/AddressScreen/input_HouseNumber'),
            "Unit": findTestObject('Object Repository/AddressScreen/input_Unit'),
            "City": findTestObject('Object Repository/AddressScreen/input_City'),
            "ZIP Code": findTestObject('Object Repository/AddressScreen/input_ZipCode'),
            "State": findTestObject('Object Repository/AddressScreen/input_State'),
            "Country": findTestObject('Object Repository/AddressScreen/input_Country')
        ]

        expectedFields.each { fieldName, expectedValue ->
            if (fieldMappings.containsKey(fieldName)) {
                def fieldObject = fieldMappings.get(fieldName)
                Mobile.waitForElementPresent(fieldObject, 10, FailureHandling.OPTIONAL)
                String actualValue = Mobile.getText(fieldObject, 5, FailureHandling.OPTIONAL)
                
                // Handle empty/null expected values gracefully, as some fields might be optional
                if (expectedValue == null || expectedValue.trim().isEmpty()) {
                    if (actualValue != null && !actualValue.trim().isEmpty()) {
                        KeywordUtil.markFailed("Field '${fieldName}': Expected empty, but found '${actualValue}'")
                    } else {
                        KeywordUtil.logInfo("Field '${fieldName}': Expected empty, found empty (correct).")
                    }
                } else {
                    Mobile.verifyElementText(fieldObject, expectedValue, FailureHandling.CONTINUE_ON_FAILURE)
                    if (actualValue == expectedValue) {
                        KeywordUtil.logInfo("Field '${fieldName}': Verified as '${actualValue}' (correct).")
                    } else {
                        KeywordUtil.markFailed("Field '${fieldName}': Expected '${expectedValue}', but found '${actualValue}'")
                    }
                }
            } else {
                KeywordUtil.logWarning("Unknown field name in expectedFields: '${fieldName}'")
            }
        }
        KeywordUtil.logInfo("Address field verification completed.")
    }

    /**
     * Edits a specific allowed address field.
     * @param fieldName The name of the field to edit (e.g., "Street Name", "Unit").
     * @param newValue The new value to set.
     */
    @com.kms.katalon.core.annotation.Keyword
    def editAddressField(String fieldName, String newValue) {
        KeywordUtil.logInfo("Editing field '${fieldName}' to '${newValue}'")
        def fieldObject
        switch (fieldName) {
            case "Street Name":
                fieldObject = findTestObject('Object Repository/AddressScreen/input_StreetName')
                break
            case "House Number":
                fieldObject = findTestObject('Object Repository/AddressScreen/input_HouseNumber')
                break
            case "Unit":
                fieldObject = findTestObject('Object Repository/AddressScreen/input_Unit')
                break
            case "City": // Assuming City is editable for some edge cases, though FRD says street/house/unit/neighborhood
                fieldObject = findTestObject('Object Repository/AddressScreen/input_City')
                break
            default:
                KeywordUtil.markFailed("Editing of field '${fieldName}' is not supported or locator not defined.")
                return
        }
        Mobile.waitForElementPresent(fieldObject, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.setText(fieldObject, newValue, 10)
        Mobile.hideKeyboard()
        KeywordUtil.logInfo("Field '${fieldName}' updated to '${newValue}'.")
    }

    /**
     * Taps the 'Continue' button on the address entry/review screen.
     */
    @com.kms.katalon.core.annotation.Keyword
    def tapContinue() {
        KeywordUtil.logInfo("Tapping 'Continue' button.")
        Mobile.tap(findTestObject('Object Repository/AddressScreen/btn_Continue'), 10)
        Mobile.delay(2) // Wait for next screen or API response
    }

    /**
     * Verifies if an error message is displayed with the expected text.
     * @param message The expected error message text.
     */
    @com.kms.katalon.core.annotation.Keyword
    def verifyErrorMessage(String message) {
        KeywordUtil.logInfo("Verifying error message: '${message}'")
        def errorObject = findTestObject('Object Repository/AddressScreen/text_ErrorMessage')
        Mobile.waitForElementPresent(errorObject, 15, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(errorObject, message, FailureHandling.STOP_ON_FAILURE)
        KeywordUtil.logInfo("Error message verified: '${message}'")
    }
    
    /**
     * Verifies the presence and content of the address confirmation screen.
     * @param userAddress The expected address entered/edited by the user.
     * @param melissaAddress The expected address recommended by Melissa.
     */
    @com.kms.katalon.core.annotation.Keyword
    def verifyConfirmationScreen(String userAddress, String melissaAddress) {
        KeywordUtil.logInfo("Verifying Address Confirmation Screen...")
        Mobile.waitForElementPresent(findTestObject('Object Repository/AddressConfirmationScreen/text_UserAddress'), 15, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(findTestObject('Object Repository/AddressConfirmationScreen/text_UserAddress'), userAddress, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(findTestObject('Object Repository/AddressConfirmationScreen/text_MelissaAddress'), melissaAddress, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(findTestObject('Object Repository/AddressConfirmationScreen/btn_ProceedWithUserAddress'), 5, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(findTestObject('Object Repository/AddressConfirmationScreen/btn_ProceedWithMelissaAddress'), 5, FailureHandling.STOP_ON_FAILURE)
        KeywordUtil.logInfo("Address Confirmation Screen verified.")
    }

    /**
     * Selects an option on the address confirmation screen.
     * @param option "User" to proceed with user's address, "Melissa" to proceed with Melissa's address.
     */
    @com.kms.katalon.core.annotation.Keyword
    def selectConfirmationOption(String option) {
        KeywordUtil.logInfo("Selecting confirmation option: '${option}'")
        if (option.equalsIgnoreCase("User")) {
            Mobile.tap(findTestObject('Object Repository/AddressConfirmationScreen/btn_ProceedWithUserAddress'), 10)
        } else if (option.equalsIgnoreCase("Melissa")) {
            Mobile.tap(findTestObject('Object Repository/AddressConfirmationScreen/btn_ProceedWithMelissaAddress'), 10)
        } else {
            KeywordUtil.markFailed("Invalid confirmation option: '${option}'. Must be 'User' or 'Melissa'.")
        }
        Mobile.delay(2) // Wait for next screen
    }
    
    /**
     * Verifies that the user has proceeded to the next onboarding step.
     * This is a generic check and might need to be more specific based on the actual next screen.
     */
    @com.kms.katalon.core.annotation.Keyword
    def verifyProceededToNextStep() {
        KeywordUtil.logInfo("Verifying navigation to the next onboarding step...")
        // Example: Check for a unique element on the next screen, e.g., a header or a button.
        // For demonstration, we'll assume a generic 'Next Step' header appears.
        Mobile.waitForElementPresent(findTestObject('Object Repository/Common/text_NextStepHeader'), 20, FailureHandling.STOP_ON_FAILURE)
        KeywordUtil.logInfo("Successfully proceeded to the next onboarding step.")
    }

    /**
     * Placeholder for GA4 analytics event validation.
     * In a real scenario, this would involve intercepting network requests,
     * parsing GA4 payloads, and validating event names and parameters.
     * For Katalon, this often requires integration with a proxy tool (e.g., BrowserMob Proxy)
     * or a custom solution to tap into network traffic.
     * @param eventName The expected GA4 event name.
     * @param eventParams A map of expected event parameters.
     */
    @com.kms.katalon.core.annotation.Keyword
    def validateGA4Event(String eventName, Map<String, String> eventParams = [:]) {
        KeywordUtil.logWarning("GA4 event validation is a placeholder. Actual implementation requires network interception or SDK integration.")
        KeywordUtil.logInfo("Simulating GA4 event validation for: '${eventName}' with params: ${eventParams}")
        // In a real scenario, you would:
        // 1. Start a network proxy (e.g., BrowserMob Proxy).
        // 2. Configure the mobile device/emulator to use the proxy.
        // 3. Perform actions that trigger the GA4 event.
        // 4. Intercept network requests and filter for GA4/Firebase analytics endpoints.
        // 5. Parse the request body/payload to extract event name and parameters.
        // 6. Assert that the extracted eventName matches and all eventParams are present and correct.
        KeywordUtil.logInfo("GA4 event validation for '${eventName}' simulated successfully.")
    }
}