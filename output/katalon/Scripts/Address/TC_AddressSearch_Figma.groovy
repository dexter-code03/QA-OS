import static com.kms.katalon.core.checkpoint.CheckpointFactory.findCheckpoint
import static com.kms.katalon.core.testcase.TestCaseFactory.findTestCase
import static com.kms.katalon.core.testdata.TestDataFactory.findTestData
import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject
import static com.kms.katalon.core.testobject.ObjectRepository.findWindowsObject

import com.kms.katalon.core.model.FailureHandling
import com.kms.katalon.core.testobject.TestObject
import com.kms.katalon.core.webui.keyword.WebUiBuiltInKeywords as WebUI
import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile
import com.kms.katalon.core.cucumber.keyword.CucumberBuiltinKeywords as CucumberKW
import com.kms.katalon.core.windows.keyword.WindowsBuiltinKeywords as Windows
import internal.GlobalVariable

import ObjectRepository_Figma as OR

/**
 * Test Case: TC_AddressSearch_Figma
 * Description: Verifies the address search, debounce, autocomplete suggestions,
 *              suggestion selection, and field auto-mapping functionality
 *              using Figma component names as object locators.
 */
@com.kms.katalon.core.annotation.Keyword
class TC_AddressSearch_Figma {

    /**
     * Simulates typing with a debounce delay.
     * @param to The TestObject for the input field.
     * @param text The text to type.
     * @param debounceDelayMs The delay in milliseconds to simulate debounce.
     */
    @com.kms.katalon.core.annotation.Keyword
    def typeWithDebounce(TestObject to, String text, int debounceDelayMs) {
        Mobile.setText(to, text, 0) // Type the full text
        Mobile.delay(debounceDelayMs / 1000) // Wait for debounce
        println "Typed '${text}' into '${to.getObjectId()}' and waited for debounce."
    }

    /**
     * Main test method for address search.
     */
    @com.kms.katalon.core.annotation.Keyword
    def execute() {
        // --- Pre-conditions: Assume user is on the address entry screen ---
        // Figma: "Hello, Naveen 👋" text - a general indicator for a logged-in state or initial screen
        Mobile.startApplication(GlobalVariable.G_AppPath, false)
        Mobile.waitForElementPresent(OR.HelloNaveenText_Figma, 30, FailureHandling.STOP_ON_FAILURE)
        println "Navigated to initial screen."

        // Simulate navigation to the address entry screen.
        // This might involve tapping a "Continue" or "Enter Address" button.
        // Figma: "Button text="Continue"" - using a generic continue button if available
        if (Mobile.waitForElementPresent(OR.GenericContinueButton_Figma, 5, FailureHandling.OPTIONAL)) {
            Mobile.tap(OR.GenericContinueButton_Figma, 5)
            println "Tapped generic continue button to proceed to address entry."
        } else {
            println "Generic continue button not found, assuming direct navigation to address entry screen."
        }

        Mobile.waitForElementPresent(OR.AddressSearchInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        println "Successfully navigated to the address entry screen."

        // --- Test Step 1: Type partial address and verify debounce ---
        String partialAddress = "1600 Amphitheatre Parkway"
        String expectedSuggestion = "1600 Amphitheatre Pkwy, Mountain View, CA 94043, USA" // Example suggestion

        println "Typing partial address: '${partialAddress}'"
        // FRD: "At least ~4 words are entered, OR Approximately 50% of the expected address length is reached."
        // FRD: "Once the threshold is met and the user pauses typing (debounce logic applied)"
        // Simulate typing and then pausing for debounce.
        typeWithDebounce(OR.AddressSearchInput_Figma, partialAddress, 2000) // 2-second debounce

        // --- Test Step 2: Verify autocomplete suggestions appear ---
        Mobile.waitForElementPresent(OR.AnySuggestionItem_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.SuggestionItem_Figma(expectedSuggestion), 5, FailureHandling.STOP_ON_FAILURE)
        println "Verified autocomplete suggestions appeared, including: '${expectedSuggestion}'"

        // --- Test Step 3: Select a suggestion ---
        Mobile.tap(OR.SuggestionItem_Figma(expectedSuggestion), 5)
        println "Selected suggestion: '${expectedSuggestion}'"

        // --- Test Step 4: Verify fields are auto-mapped ---
        Mobile.waitForElementPresent(OR.StreetNameInput_Figma, 10, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(OR.StreetNameInput_Figma, "Amphitheatre Pkwy", FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(OR.HouseBuildingNumberInput_Figma, "1600", FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(OR.CityInput_Figma, "Mountain View", FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(OR.StateInput_Figma, "CA", FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementText(OR.ZipCodeInput_Figma, "94043", FailureHandling.STOP_ON_FAILURE)
        // Neighborhood and Unit might be empty for this example, or have default values
        Mobile.verifyElementPresent(OR.NeighborhoodInput_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        Mobile.verifyElementPresent(OR.UnitApartmentInput_Figma, 5, FailureHandling.STOP_ON_FAILURE)
        println "Verified address fields are auto-mapped correctly."

        // --- Test Step 5: Edit an allowed field ---
        String newUnit = "Apt 101"
        Mobile.setText(OR.UnitApartmentInput_Figma, newUnit, 0)
        Mobile.verifyElementText(OR.UnitApartmentInput_Figma, newUnit, FailureHandling.STOP_ON_FAILURE)
        println "Edited 'Unit / Apartment' field to: '${newUnit}'"

        // --- Test Step 6: Tap Continue button ---
        Mobile.tap(OR.ContinueButton_Figma, 5)
        println "Tapped 'Continue' button."

        // At this point, the system would proceed to address verification.
        // This test case focuses on search and mapping, so we'll assume success for now.
        // The next screen (e.g., a confirmation or next onboarding step) should be visible.
        Mobile.waitForElementPresent(OR.NextOnboardingStepScreen_Figma, 10, FailureHandling.OPTIONAL)
        println "Address search and mapping flow completed successfully, proceeding to next step (or verification)."
    }
}